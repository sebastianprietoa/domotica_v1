from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Callable, Protocol

from ambilight_tuya.models import CommandProfile, DeviceStatus, RGBColor, TuyaCredentials
from tuya_connector import TuyaOpenAPI


logger = logging.getLogger(__name__)


class OpenApiProtocol(Protocol):
    token_info: Any

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
    api_factory: Callable[[str, str, str], OpenApiProtocol] = TuyaOpenAPI

    def __post_init__(self) -> None:
        self._api = self.api_factory(
            self.credentials.api_endpoint,
            self.credentials.access_id,
            self.credentials.access_key,
        )
        self._connected = False

    def connect(self) -> None:
        response = self._api.connect()
        if not response or not response.get("success"):
            raise TuyaApiError(f"Unable to authenticate with Tuya: {response}")
        self._connected = True

    def _ensure_connected(self) -> None:
        if not self._connected:
            self.connect()

    def list_devices(self) -> list[dict[str, Any]]:
        self._ensure_connected()
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

        if uid:
            user_response = self._api.get(f"/v1.0/users/{uid}/devices")
            self._require_success(user_response)
            return list(user_response.get("result", []))

        raise TuyaApiError(
            "Unable to list devices from Tuya Cloud and authenticated token does not expose a user uid"
        )

    def get_device_status(self, device_id: str) -> DeviceStatus:
        self._ensure_connected()
        response = self._api.get(f"/v1.0/iot-03/devices/{device_id}/status")
        self._require_success(response)
        result = response.get("result", [])
        online = any(item.get("value") for item in result if item.get("code") in {"switch_led", "switch", "online"})
        return DeviceStatus(device_id=device_id, online=bool(online), raw={"status": result})

    def send_commands(self, device_id: str, commands: list[dict[str, Any]]) -> dict[str, Any]:
        self._ensure_connected()
        response = self._api.post(
            f"/v1.0/iot-03/devices/{device_id}/commands",
            {"commands": commands},
        )
        self._require_success(response)
        return response

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
