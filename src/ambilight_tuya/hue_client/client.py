from __future__ import annotations

import colorsys
from dataclasses import dataclass
import logging
from typing import Any

import requests
import urllib3

from ambilight_tuya.models import DeviceStatus, HueCredentials, RGBColor


logger = logging.getLogger(__name__)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class HueApiError(RuntimeError):
    """Raised when the local Hue Bridge rejects a request."""


@dataclass
class HueClient:
    credentials: HueCredentials
    session_factory: type[requests.Session] = requests.Session

    def __post_init__(self) -> None:
        self._session = self.session_factory()
        self._session.verify = False
        self._session.headers.update({"Content-Type": "application/json"})
        self._base_url = f"https://{self.credentials.bridge_ip}/api/{self.credentials.application_key}"
        self._last_request: dict[str, Any] | None = None

    def debug_snapshot(self) -> dict[str, Any]:
        return {
            "bridge_ip": self.credentials.bridge_ip,
            "configured": bool(self.credentials.bridge_ip and self.credentials.application_key),
            "application_key_suffix": self.credentials.application_key[-6:],
            "last_request": self._last_request,
        }

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        url = f"{self._base_url}{path}"
        response = self._session.request(method, url, json=body, timeout=5)
        self._last_request = {
            "method": method,
            "path": path,
            "http_status": response.status_code,
        }
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            errors = [
                entry.get("error")
                for entry in payload
                if isinstance(entry, dict) and isinstance(entry.get("error"), dict)
            ]
            if errors:
                raise HueApiError(f"Hue Bridge request failed: {errors[0]}")
        elif isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            raise HueApiError(f"Hue Bridge request failed: {payload['error']}")
        return payload

    def get_full_state(self) -> dict[str, Any]:
        payload = self._request("GET", "/")
        if not isinstance(payload, dict):
            raise HueApiError("Hue Bridge did not return a valid bridge state document.")
        return payload

    @staticmethod
    def _room_map_from_groups(groups: dict[str, Any]) -> dict[str, str]:
        room_priority = {"Room": 0, "Zone": 1, "LightGroup": 2}
        resolved: dict[str, tuple[int, str]] = {}
        for group in groups.values():
            if not isinstance(group, dict):
                continue
            group_type = str(group.get("type", "")).strip()
            group_name = str(group.get("name", "")).strip()
            if group_type not in room_priority or not group_name:
                continue
            for light_id in group.get("lights", []):
                key = str(light_id)
                current = resolved.get(key)
                candidate = (room_priority[group_type], group_name)
                if current is None or candidate[0] < current[0]:
                    resolved[key] = candidate
        return {
            light_id: room_name
            for light_id, (_, room_name) in resolved.items()
        }

    def list_lights(self) -> list[dict[str, Any]]:
        bridge_state = self.get_full_state()
        lights = bridge_state.get("lights", {})
        groups = bridge_state.get("groups", {})
        room_map = self._room_map_from_groups(groups if isinstance(groups, dict) else {})
        light_list: list[dict[str, Any]] = []
        for light_id, light_data in (lights.items() if isinstance(lights, dict) else []):
            if not isinstance(light_data, dict):
                continue
            light_list.append(
                {
                    "id": str(light_id),
                    "name": str(light_data.get("name", f"Hue {light_id}")).strip(),
                    "type": str(light_data.get("type", "Hue light")).strip(),
                    "modelid": str(light_data.get("modelid", "")).strip(),
                    "productname": str(light_data.get("productname", "")).strip(),
                    "state": dict(light_data.get("state", {})),
                    "config": dict(light_data.get("config", {})),
                    "room": room_map.get(str(light_id)),
                    "raw": light_data,
                }
            )
        return light_list

    def get_light_status(self, light_id: str) -> DeviceStatus:
        light = self._request("GET", f"/lights/{light_id}")
        if not isinstance(light, dict):
            raise HueApiError("Hue light status payload was not valid.")
        state = dict(light.get("state", {}))
        reachable = bool(state.get("reachable", light.get("config", {}).get("reachable", True)))
        power_state = "on" if bool(state.get("on")) else "off"
        return DeviceStatus(
            device_id=str(light_id),
            online=reachable,
            raw={
                "status": state,
                "status_map": state,
                "power_state": power_state,
                "room": light.get("room"),
                "light": light,
            },
        )

    def get_light_capabilities(self, light_id: str, light: dict[str, Any] | None = None) -> dict[str, Any]:
        resolved_light = light
        if resolved_light is None:
            payload = self._request("GET", f"/lights/{light_id}")
            if not isinstance(payload, dict):
                raise HueApiError("Hue light capability payload was not valid.")
            resolved_light = {
                "id": str(light_id),
                "state": dict(payload.get("state", {})),
                "type": payload.get("type"),
                "modelid": payload.get("modelid"),
                "productname": payload.get("productname"),
                "raw": payload,
            }
        state = dict(resolved_light.get("state", {}))
        brightness_supported = "bri" in state
        color_supported = any(code in state for code in ("hue", "sat", "xy", "colormode"))
        return {
            "power_supported": "on" in state,
            "brightness_supported": brightness_supported,
            "brightness_code": "bri" if brightness_supported else None,
            "brightness_min": 1 if brightness_supported else None,
            "brightness_max": 254 if brightness_supported else None,
            "current_brightness": int(state["bri"]) if brightness_supported else None,
            "color_supported": color_supported,
            "status_map": state,
            "room": resolved_light.get("room"),
            "type": resolved_light.get("type"),
        }

    def set_power_state(self, light_id: str, is_on: bool) -> dict[str, Any]:
        response = self._request(
            "PUT",
            f"/lights/{light_id}/state",
            {"on": bool(is_on), "transitiontime": 0},
        )
        return {"response": response, "power_code": "on"}

    def set_brightness(self, light_id: str, level: int) -> dict[str, Any]:
        clamped_level = max(0, min(int(level), 100))
        bri = max(1, round((clamped_level / 100.0) * 254))
        response = self._request(
            "PUT",
            f"/lights/{light_id}/state",
            {
                # Hue keeps hue/sat/xy state intact when only bri changes.
                "bri": bri,
                "transitiontime": 0,
            },
        )
        return {
            "response": response,
            "brightness_code": "bri",
            "target_value": bri,
            "level": clamped_level,
            "strategy": "hue_bri_only",
        }

    def set_fixed_color(self, light_id: str, color: RGBColor) -> dict[str, Any]:
        red = color.r / 255.0
        green = color.g / 255.0
        blue = color.b / 255.0
        hue, saturation, value = colorsys.rgb_to_hsv(red, green, blue)
        response = self._request(
            "PUT",
            f"/lights/{light_id}/state",
            {
                "on": True,
                "hue": round(hue * 65535),
                "sat": round(saturation * 254),
                "bri": max(1, round(value * 254)),
                "transitiontime": 0,
            },
        )
        return {
            "response": response,
            "color_code": "hue_sat_bri",
        }
