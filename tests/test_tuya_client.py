from __future__ import annotations

from dataclasses import dataclass

from ambilight_tuya.models import CommandProfile, RGBColor, TuyaCredentials
from ambilight_tuya.tuya_client import TuyaClient


@dataclass
class FakeTokenInfo:
    uid: str = "user-1"


class FakeOpenAPI:
    def __init__(
        self,
        endpoint: str,
        access_id: str,
        access_key: str,
        auth_scheme: str = "auto",
        app_identifier: str | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.access_id = access_id
        self.access_key = access_key
        self.resolved_auth_scheme = "cloud" if auth_scheme == "auto" else auth_scheme
        self.token_info = FakeTokenInfo()
        self.commands: list[tuple[str, dict]] = []

    def connect(self) -> dict:
        return {"success": True}

    def get(self, path: str, params: dict | None = None) -> dict:
        if path.endswith("/specification"):
            return {
                "success": True,
                "result": {
                    "category": "dj",
                    "functions": [
                        {"code": "switch_led", "type": "Boolean", "values": "{}"},
                        {"code": "bright_value_v2", "type": "Integer", "values": "{\"min\":10,\"max\":1000}"},
                        {"code": "work_mode", "type": "Enum", "values": "{\"range\":[\"white\",\"colour\"]}"},
                        {"code": "colour_data_v2", "type": "Json", "values": "{}"},
                    ],
                    "status": [
                        {"code": "switch_led", "type": "Boolean", "values": "{}"},
                        {"code": "bright_value_v2", "type": "Integer", "values": "{\"min\":10,\"max\":1000}"},
                    ],
                },
            }
        if path == "/v1.0/iot-01/associated-users/devices":
            return {
                "success": True,
                "result": {
                    "devices": [{"id": "device-app-1", "name": "smart life bulb"}],
                    "total": 1,
                },
            }
        if path == "/v1.3/iot-03/devices":
            return {
                "success": True,
                "result": {
                    "list": [{"id": "device-1", "name": "living 2"}],
                    "total": 1,
                },
            }
        if path == "/v1.0/expand/devices":
            return {"success": True, "result": [{"id": "device-1", "name": "living 2"}]}
        if path.endswith("/devices"):
            return {"success": True, "result": [{"id": "device-1"}]}
        return {
            "success": True,
            "result": [
                {"code": "switch_led", "value": True},
                {"code": "bright_value_v2", "value": 505},
                {"code": "work_mode", "value": "colour"},
                {"code": "colour_data_v2", "value": {"h": 360, "s": 1000, "v": 1000}},
            ],
        }

    def post(self, path: str, body: dict | None = None) -> dict:
        self.commands.append((path, body or {}))
        return {"success": True, "result": True}


def test_tuya_client_lists_devices() -> None:
    client = TuyaClient(
        TuyaCredentials("id", "key", "https://example.com"),
        api_factory=FakeOpenAPI,
    )
    assert client.list_devices() == [{"id": "device-1", "name": "living 2"}]


class FakeOpenAPIFallback(FakeOpenAPI):
    def get(self, path: str, params: dict | None = None) -> dict:
        if path == "/v1.3/iot-03/devices":
            return {"success": False, "code": 1106, "msg": "permission deny"}
        if path == "/v1.0/expand/devices":
            return {"success": False, "code": 1106, "msg": "permission deny"}
        return super().get(path, params)


def test_tuya_client_falls_back_to_user_devices() -> None:
    client = TuyaClient(
        TuyaCredentials("id", "key", "https://example.com"),
        api_factory=FakeOpenAPIFallback,
    )
    assert client.list_devices() == [{"id": "device-1"}]


def test_tuya_client_lists_devices_for_app_authorization() -> None:
    client = TuyaClient(
        TuyaCredentials(
            "id",
            "key",
            "https://example.com",
            auth_scheme="app",
            app_identifier="com.sebastianprietoa.ambilight.localhost",
        ),
        api_factory=FakeOpenAPI,
    )
    assert client.list_devices() == [{"id": "device-app-1", "name": "smart life bulb"}]


def test_tuya_client_sets_color() -> None:
    client = TuyaClient(
        TuyaCredentials("id", "key", "https://example.com"),
        api_factory=FakeOpenAPI,
    )
    client.set_fixed_color("device-1", RGBColor(255, 0, 0), CommandProfile())

    assert client._api.commands[0][0] == "/v1.0/iot-03/devices/device-1/commands"
    assert client._api.commands[0][1]["commands"][0]["code"] == "switch_led"


def test_tuya_client_sets_power() -> None:
    client = TuyaClient(
        TuyaCredentials("id", "key", "https://example.com"),
        api_factory=FakeOpenAPI,
    )

    client.set_power_state("device-1", True, CommandProfile())

    assert client._api.commands[0][0] == "/v1.0/iot-03/devices/device-1/commands"
    assert client._api.commands[0][1]["commands"] == [{"code": "switch_led", "value": True}]


def test_tuya_client_reads_capabilities() -> None:
    client = TuyaClient(
        TuyaCredentials("id", "key", "https://example.com"),
        api_factory=FakeOpenAPI,
    )

    capabilities = client.get_device_capabilities("device-1")

    assert capabilities["power_supported"] is True
    assert capabilities["brightness_supported"] is True
    assert capabilities["brightness_code"] == "bright_value_v2"
    assert capabilities["current_brightness"] == 505
    assert capabilities["color_supported"] is True


def test_tuya_client_sets_brightness() -> None:
    client = TuyaClient(
        TuyaCredentials("id", "key", "https://example.com"),
        api_factory=FakeOpenAPI,
    )

    result = client.set_brightness("device-1", 50)

    assert result["brightness_code"] == "bright_value_v2"
    assert result["strategy"] == "preserve_color_payload"
    assert client._api.commands[0][1]["commands"][0]["code"] == "work_mode"
    assert client._api.commands[0][1]["commands"][1]["code"] == "colour_data_v2"
    assert client._api.commands[0][1]["commands"][1]["value"]["h"] == 360
    assert client._api.commands[0][1]["commands"][1]["value"]["s"] == 1000
