from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Callable, Protocol

from ambilight_tuya.models import CommandProfile, DeviceStatus, RGBColor, TuyaCredentials
from tuya_connector import TuyaOpenAPI


logger = logging.getLogger(__name__)

POWER_STATUS_CODES = ("switch_led", "switch", "switch_1")


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

    def send_commands(self, device_id: str, commands: list[dict[str, Any]]) -> dict[str, Any]:
        self._ensure_connected()
        path = (
            f"/v1.0/devices/{device_id}/commands"
            if self._uses_app_authorization()
            else f"/v1.0/iot-03/devices/{device_id}/commands"
        )
        response = self._api.post(
            path,
            {"commands": commands},
        )
        self._require_success(response)
        return response

    def set_power_state(self, device_id: str, is_on: bool, profile: CommandProfile) -> dict[str, Any]:
        command = {"code": profile.power_code, "value": bool(is_on)}
        logger.debug("Sending power state %s to device %s", is_on, device_id)
        return self.send_commands(device_id, [command])

    def set_fixed_color(self, device_id: str, color: RGBColor, profile: CommandProfile) -> dict[str, Any]:
        hsv = color.to_hsv().as_dict()
        commands = [
            {"code": profile.power_code, "value": True},
            {"code": profile.color_mode_code, "value": profile.color_mode_value},
            {"code": profile.color_data_code, "value": hsv},
        ]
        logger.debug("Sending color %s to device %s", color.as_tuple(), device_id)
        return self.send_commands(device_id, commands)

    @staticmethod
    def _require_success(response: dict[str, Any] | None) -> None:
        if not response or not response.get("success"):
            raise TuyaApiError(f"Tuya API request failed: {response}")
