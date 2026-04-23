from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re
from typing import Any, Callable, Protocol

from ambilight_tuya.models import CommandProfile, DeviceStatus, RGBColor, TuyaCredentials
from tuya_connector import TuyaOpenAPI


logger = logging.getLogger(__name__)

POWER_STATUS_CODES = ("switch_led", "switch", "switch_1")
BRIGHTNESS_CODES = ("bright_value_v2", "bright_value", "bright_value_1", "bright_value_2")
COLOR_MODE_CODES = ("work_mode",)
COLOR_DATA_CODES = ("colour_data_v2", "colour_data")


class OpenApiProtocol(Protocol):
    token_info: Any
    resolved_auth_scheme: str

    def connect(self) -> dict[str, Any]:
        ...

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        ...

    def post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        ...


class TuyaApiError(RuntimeError):
    """Raised when Tuya returns an error."""


@dataclass
class TuyaClient:
    credentials: TuyaCredentials
    api_factory: Callable[..., OpenApiProtocol] = TuyaOpenAPI

    def __post_init__(self) -> None:
        self._api = self.api_factory(
            self.credentials.api_endpoint,
            self.credentials.access_id,
            self.credentials.access_key,
            auth_scheme=self.credentials.auth_scheme,
            app_identifier=self.credentials.app_identifier,
        )
        self._connected = False

    def connect(self) -> None:
        response = self._api.connect()
        if not response or not response.get("success"):
            raise TuyaApiError(f"Unable to authenticate with Tuya: {response}")
        self._connected = True

    def connect_with_authorization_code(self, code: str) -> dict[str, Any]:
        connect_with_code = getattr(self._api, "connect_with_authorization_code", None)
        if connect_with_code is None:
            raise TuyaApiError("Current Tuya API client does not support OAuth 2.0 authorization codes")
        response = connect_with_code(code)
        if not response or not response.get("success"):
            raise TuyaApiError(f"Unable to exchange OAuth authorization code with Tuya: {response}")
        self._connected = True
        return response

    def restore_token_response(self, token_response: dict[str, Any]) -> None:
        restore_token = getattr(self._api, "restore_token", None)
        if restore_token is None:
            raise TuyaApiError("Current Tuya API client does not support restoring OAuth tokens")
        restore_token(token_response)
        self._connected = True

    def debug_snapshot(self) -> dict[str, Any]:
        access_id = self.credentials.access_id
        return {
            "api_endpoint": self.credentials.api_endpoint,
            "configured_auth_scheme": self.credentials.auth_scheme,
            "resolved_auth_scheme": getattr(self._api, "resolved_auth_scheme", self.credentials.auth_scheme),
            "app_identifier": self.credentials.app_identifier,
            "client_id_suffix": access_id[-6:] if len(access_id) >= 6 else access_id,
            "connected": self._connected,
            "uid": getattr(getattr(self._api, "token_info", None), "uid", None),
            "last_connect_attempts": getattr(self._api, "last_connect_attempts", []),
            "last_request": getattr(self._api, "last_request_summary", None),
        }

    def _ensure_connected(self) -> None:
        if not self._connected:
            self.connect()

    def _uses_app_authorization(self) -> bool:
        return getattr(self._api, "resolved_auth_scheme", self.credentials.auth_scheme) == "app"

    @staticmethod
    def _parse_values_definition(raw_values: Any) -> dict[str, Any]:
        if isinstance(raw_values, dict):
            return raw_values
        if not raw_values:
            return {}
        if isinstance(raw_values, str):
            try:
                parsed = json.loads(raw_values)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def _extract_switch_codes_from_iterable(items: list[dict[str, Any]]) -> list[str]:
        codes: list[str] = []
        for item in items:
            code = str(item.get("code", "")).strip()
            if not code:
                continue
            if code == "switch_led" or code == "switch" or re.fullmatch(r"switch_\d+", code):
                codes.append(code)
        return codes

    @staticmethod
    def _unique_codes(*groups: list[str]) -> list[str]:
        ordered: list[str] = []
        for group in groups:
            for code in group:
                if code and code not in ordered:
                    ordered.append(code)
        return ordered

    def _command_path(self, device_id: str) -> str:
        return (
            f"/v1.0/devices/{device_id}/commands"
            if self._uses_app_authorization()
            else f"/v1.0/iot-03/devices/{device_id}/commands"
        )

    def _post_commands_raw(self, device_id: str, commands: list[dict[str, Any]]) -> dict[str, Any]:
        self._ensure_connected()
        return self._api.post(self._command_path(device_id), {"commands": commands})

    def list_devices(self) -> list[dict[str, Any]]:
        self._ensure_connected()
        if self._uses_app_authorization():
            app_response = self._api.get(
                "/v1.0/iot-01/associated-users/devices",
                {"size": 200},
            )
            self._require_success(app_response)
            result = app_response.get("result", {})
            return list(result.get("devices", []))

        uid = getattr(self._api.token_info, "uid", "")
        if uid:
            scoped_response = self._api.get(
                "/v1.3/iot-03/devices",
                {
                    "source_type": "tuyaUser",
                    "source_id": uid,
                    "page_size": 200,
                },
            )
            if scoped_response and scoped_response.get("success"):
                result = scoped_response.get("result", {})
                if isinstance(result, dict):
                    return list(result.get("list", []))

            user_response = self._api.get(f"/v1.0/users/{uid}/devices")
            self._require_success(user_response)
            return list(user_response.get("result", []))

        raise TuyaApiError("Unable to list devices from Tuya Cloud and authenticated token does not expose a user uid")

    def get_device_status(self, device_id: str) -> DeviceStatus:
        self._ensure_connected()
        path = (
            f"/v1.0/devices/{device_id}/status"
            if self._uses_app_authorization()
            else f"/v1.0/iot-03/devices/{device_id}/status"
        )
        response = self._api.get(path)
        self._require_success(response)
        result = response.get("result", [])
        status_map = {
            item.get("code"): item.get("value")
            for item in result
            if item.get("code")
        }
        online_value = status_map.get("online")
        if isinstance(online_value, bool):
            online = online_value
        else:
            online = True
        power_state = next(
            (
                "on" if bool(status_map[code]) else "off"
                for code in POWER_STATUS_CODES
                if code in status_map
            ),
            "unknown",
        )
        return DeviceStatus(
            device_id=device_id,
            online=bool(online),
            raw={
                "status": result,
                "status_map": status_map,
                "power_state": power_state,
            },
        )

    def get_device_specification(self, device_id: str) -> dict[str, Any]:
        self._ensure_connected()
        if self._uses_app_authorization():
            return {"functions": [], "status": [], "category": ""}
        response = self._api.get(f"/v1.0/iot-03/devices/{device_id}/specification")
        self._require_success(response)
        result = response.get("result", {})
        return result if isinstance(result, dict) else {}

    def get_device_capabilities(self, device_id: str, status: DeviceStatus | None = None) -> dict[str, Any]:
        current_status = status or self.get_device_status(device_id)
        status_map = dict(current_status.raw.get("status_map", {}))
        specification = self.get_device_specification(device_id)
        functions = list(specification.get("functions", []))
        status_definitions = list(specification.get("status", []))

        observed_switch_codes = self._extract_switch_codes_from_iterable(
            [{"code": code} for code in status_map.keys()]
        )
        function_switch_codes = self._extract_switch_codes_from_iterable(functions)
        status_switch_codes = self._extract_switch_codes_from_iterable(status_definitions)
        power_codes = self._unique_codes(
            observed_switch_codes,
            function_switch_codes,
            status_switch_codes,
        )

        color_mode_code = next(
            (
                code
                for code in self._unique_codes(
                    [code for code in COLOR_MODE_CODES if code in status_map],
                    [item.get("code", "") for item in functions],
                    [item.get("code", "") for item in status_definitions],
                )
                if code in COLOR_MODE_CODES
            ),
            None,
        )
        color_data_code = next(
            (
                code
                for code in self._unique_codes(
                    [code for code in COLOR_DATA_CODES if code in status_map],
                    [item.get("code", "") for item in functions],
                    [item.get("code", "") for item in status_definitions],
                )
                if code in COLOR_DATA_CODES
            ),
            None,
        )
        brightness_code = next(
            (
                code
                for code in self._unique_codes(
                    [code for code in BRIGHTNESS_CODES if code in status_map],
                    [item.get("code", "") for item in functions],
                    [item.get("code", "") for item in status_definitions],
                )
                if code in BRIGHTNESS_CODES
            ),
            None,
        )
        if brightness_code is None and bool(color_mode_code and color_data_code):
            brightness_code = "bright_value_v2" if color_data_code == "colour_data_v2" else "bright_value"
        brightness_definition = next(
            (
                item
                for item in [*functions, *status_definitions]
                if item.get("code") == brightness_code
            ),
            {},
        )
        brightness_values = self._parse_values_definition(brightness_definition.get("values"))
        brightness_min = int(brightness_values.get("min", 10 if brightness_code and brightness_code.endswith("_v2") else 0))
        brightness_max = int(brightness_values.get("max", 1000 if brightness_code and brightness_code.endswith("_v2") else 255))

        current_brightness = None
        if brightness_code and brightness_code in status_map:
            try:
                current_brightness = int(status_map[brightness_code])
            except (TypeError, ValueError):
                current_brightness = None

        return {
            "category": specification.get("category", ""),
            "power_supported": bool(power_codes),
            "power_codes": power_codes,
            "brightness_supported": brightness_code is not None or bool(color_mode_code and color_data_code),
            "brightness_code": brightness_code,
            "brightness_min": brightness_min,
            "brightness_max": brightness_max,
            "current_brightness": current_brightness,
            "color_supported": bool(color_mode_code and color_data_code),
            "color_mode_code": color_mode_code,
            "color_data_code": color_data_code,
            "functions": functions,
            "status_definitions": status_definitions,
            "status_map": status_map,
        }

    def send_commands(self, device_id: str, commands: list[dict[str, Any]]) -> dict[str, Any]:
        response = self._post_commands_raw(device_id, commands)
        self._require_success(response)
        return response

    def set_power_state(
        self,
        device_id: str,
        is_on: bool,
        profile: CommandProfile,
        capabilities: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_capabilities = capabilities or self.get_device_capabilities(device_id)
        power_codes = self._unique_codes(
            [profile.power_code] if profile.power_code in resolved_capabilities.get("power_codes", []) else [],
            list(resolved_capabilities.get("power_codes", [])),
        )
        if not power_codes:
            raise TuyaApiError("This device does not expose a supported power switch datapoint.")

        last_response: dict[str, Any] | None = None
        attempted_codes: list[str] = []
        for power_code in power_codes:
            attempted_codes.append(power_code)
            logger.debug("Sending power state %s to device %s using %s", is_on, device_id, power_code)
            response = self._post_commands_raw(device_id, [{"code": power_code, "value": bool(is_on)}])
            if response and response.get("success"):
                return {
                    "response": response,
                    "power_code": power_code,
                    "attempted_codes": attempted_codes,
                }
            last_response = response
            if response and response.get("code") != 2008:
                self._require_success(response)

        raise TuyaApiError(
            "No supported power command succeeded for this device. "
            f"Attempted datapoints: {', '.join(attempted_codes)}. Last response: {last_response}"
        )

    def set_brightness(
        self,
        device_id: str,
        level: int,
        capabilities: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_capabilities = capabilities or self.get_device_capabilities(device_id)
        brightness_code = resolved_capabilities.get("brightness_code")
        if not brightness_code:
            raise TuyaApiError("This device does not expose a supported brightness datapoint.")

        clamped_level = max(0, min(int(level), 100))
        min_value = int(resolved_capabilities.get("brightness_min", 0))
        max_value = int(resolved_capabilities.get("brightness_max", 255))
        if max_value <= min_value:
            target_value = max_value
        else:
            target_value = round(min_value + ((max_value - min_value) * (clamped_level / 100.0)))
        response = self._post_commands_raw(
            device_id,
            [{"code": brightness_code, "value": target_value}],
        )
        self._require_success(response)
        return {
            "response": response,
            "brightness_code": brightness_code,
            "target_value": target_value,
            "level": clamped_level,
        }

    def set_fixed_color(
        self,
        device_id: str,
        color: RGBColor,
        profile: CommandProfile,
        capabilities: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_capabilities = capabilities or self.get_device_capabilities(device_id)
        if not resolved_capabilities.get("color_supported", True):
            raise TuyaApiError("This device does not expose color datapoints.")
        power_code = next(
            iter(resolved_capabilities.get("power_codes", [])),
            profile.power_code,
        )
        color_mode_code = str(resolved_capabilities.get("color_mode_code") or profile.color_mode_code)
        color_data_code = str(resolved_capabilities.get("color_data_code") or profile.color_data_code)
        hsv = color.to_hsv().as_dict()
        commands = [
            {"code": power_code, "value": True},
            {"code": color_mode_code, "value": profile.color_mode_value},
            {"code": color_data_code, "value": hsv},
        ]
        logger.debug("Sending color %s to device %s", color.as_tuple(), device_id)
        response = self.send_commands(device_id, commands)
        return {
            "response": response,
            "power_code": power_code,
            "color_mode_code": color_mode_code,
            "color_data_code": color_data_code,
        }

    @staticmethod
    def _require_success(response: dict[str, Any] | None) -> None:
        if not response or not response.get("success"):
            raise TuyaApiError(f"Tuya API request failed: {response}")
