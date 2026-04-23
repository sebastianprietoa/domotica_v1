from __future__ import annotations

import numpy as np

from ambilight_tuya.models import AppConfig, CaptureConfig, ColorExtractionConfig, SmoothingConfig
from ambilight_tuya.models import DeviceStatus
from ambilight_tuya.models import TuyaCredentials
from ambilight_tuya.webapp.app import _parse_rgb, create_app


def test_parse_rgb_accepts_triplet() -> None:
    color = _parse_rgb("255, 80, 40")
    assert color.as_tuple() == (255, 80, 40)


def test_status_endpoint_is_available() -> None:
    app = create_app()
    client = app.test_client()

    response = client.get("/api/status")

    assert response.status_code == 200
    payload = response.get_json()
    assert "sync" in payload
    assert "oauth" in payload
    assert "oauth_callback_url" in payload


def test_oauth_config_endpoint_is_available() -> None:
    app = create_app()
    client = app.test_client()

    response = client.get("/api/tuya/oauth/config")

    assert response.status_code == 200
    payload = response.get_json()
    assert "callback_url" in payload
    assert "status" in payload


def test_debug_logs_endpoint_is_available() -> None:
    app = create_app()
    client = app.test_client()

    response = client.get("/api/debug/logs")

    assert response.status_code == 200
    payload = response.get_json()
    assert "entries" in payload


class FakeDashboardClient:
    def __init__(self, credentials) -> None:
        self.credentials = credentials

    def debug_snapshot(self) -> dict:
        return {
            "api_endpoint": self.credentials.api_endpoint,
            "configured_auth_scheme": self.credentials.auth_scheme,
            "resolved_auth_scheme": self.credentials.auth_scheme,
            "app_identifier": self.credentials.app_identifier,
            "client_id_suffix": self.credentials.access_id[-6:],
            "connected": True,
            "uid": "user-1",
            "last_connect_attempts": [],
            "last_request": None,
        }

    def list_devices(self) -> list[dict]:
        return [
            {
                "id": "device-1",
                "name": "Living 4",
                "category": "dj",
                "product_name": "Smart Bulb",
                "online": True,
                "status": [{"code": "switch_led", "value": True}],
            }
        ]

    def get_device_status(self, device_id: str) -> DeviceStatus:
        return DeviceStatus(
            device_id=device_id,
            online=True,
            raw={
                "status": [
                    {"code": "switch_led", "value": True},
                    {"code": "work_mode", "value": "colour"},
                ],
                "status_map": {
                    "switch_led": True,
                    "work_mode": "colour",
                },
                "power_state": "on",
            },
        )

    def get_device_capabilities(self, device_id: str, status: DeviceStatus | None = None) -> dict:
        return {
            "power_supported": True,
            "power_codes": ["switch_led"],
            "brightness_supported": True,
            "brightness_code": "bright_value_v2",
            "brightness_min": 10,
            "brightness_max": 1000,
            "current_brightness": 400,
            "color_supported": True,
            "color_mode_code": "work_mode",
            "color_data_code": "colour_data_v2",
        }

    def set_power_state(self, device_id: str, is_on: bool, profile, capabilities=None) -> dict:
        return {"power_code": "switch_led", "response": {"success": True}}

    def set_fixed_color(self, device_id: str, color, profile, capabilities=None) -> dict:
        return {"color_data_code": "colour_data_v2", "response": {"success": True}}

    def set_brightness(self, device_id: str, level: int, capabilities=None) -> dict:
        return {"brightness_code": "bright_value_v2", "level": level, "response": {"success": True}}


def test_list_devices_returns_normalized_cards(monkeypatch) -> None:
    monkeypatch.setattr(
        "ambilight_tuya.webapp.app.TuyaClient",
        FakeDashboardClient,
    )
    monkeypatch.setattr(
        "ambilight_tuya.webapp.app.load_tuya_credentials",
        lambda: TuyaCredentials(
            access_id="client-id-123456",
            access_key="secret",
            api_endpoint="https://example.com",
            auth_scheme="cloud",
        ),
    )
    app = create_app()
    client = app.test_client()

    response = client.post("/api/list-devices", json={})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["count"] == 1
    assert payload["devices"][0]["name"] == "Living 4"
    assert payload["devices"][0]["is_rgb_capable"] is True
    assert payload["devices"][0]["power_state"] == "on"


def test_get_device_status_returns_friendly_fields(monkeypatch) -> None:
    monkeypatch.setattr(
        "ambilight_tuya.webapp.app.TuyaClient",
        FakeDashboardClient,
    )
    monkeypatch.setattr(
        "ambilight_tuya.webapp.app.load_tuya_credentials",
        lambda: TuyaCredentials(
            access_id="client-id-123456",
            access_key="secret",
            api_endpoint="https://example.com",
            auth_scheme="cloud",
        ),
    )
    app = create_app()
    client = app.test_client()

    response = client.post("/api/get-device-status", json={"device_id": "device-1"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["device_id"] == "device-1"
    assert payload["power_state"] == "on"
    assert payload["is_rgb_capable"] is True
    assert payload["reachability_label"] == "Online"
    assert payload["brightness_supported"] is True
    assert payload["current_brightness"] == 39


def test_set_brightness_route_is_available(monkeypatch) -> None:
    monkeypatch.setattr(
        "ambilight_tuya.webapp.app.TuyaClient",
        FakeDashboardClient,
    )
    monkeypatch.setattr(
        "ambilight_tuya.webapp.app.load_tuya_credentials",
        lambda: TuyaCredentials(
            access_id="client-id-123456",
            access_key="secret",
            api_endpoint="https://example.com",
            auth_scheme="cloud",
        ),
    )
    app = create_app()
    client = app.test_client()

    response = client.post("/api/set-brightness", json={"device_id": "device-1", "level": 55})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["device_id"] == "device-1"
    assert payload["level"] == 55


def test_ambilight_preview_returns_4x4_grid(monkeypatch) -> None:
    frame = np.zeros((40, 40, 3), dtype=np.uint8)
    frame[:20, :20] = [255, 0, 0]
    frame[:20, 20:] = [0, 255, 0]
    frame[20:, :20] = [0, 0, 255]
    frame[20:, 20:] = [255, 255, 0]

    monkeypatch.setattr(
        "ambilight_tuya.webapp.app.load_project_config",
        lambda: AppConfig(
            capture=CaptureConfig(monitor_index=1, downsample=1, target_fps=8),
            extraction=ColorExtractionConfig(),
            smoothing=SmoothingConfig(alpha=0.35, min_update_interval_ms=150, min_color_delta=10.0),
        ),
    )
    monkeypatch.setattr(
        "ambilight_tuya.webapp.app.list_monitors",
        lambda: [{"left": 0, "top": 0, "width": 40, "height": 40}],
    )
    monkeypatch.setattr(
        "ambilight_tuya.webapp.app.ScreenCaptureService.capture_frame",
        lambda self: frame,
    )
    app = create_app()
    client = app.test_client()

    response = client.get("/api/ambilight-preview")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["rows"] == 4
    assert payload["cols"] == 4
    assert len(payload["cells"]) == 16
    assert payload["cells"][0]["hex"].startswith("#")
    assert payload["monitor_index"] == 1


def test_list_devices_requires_oauth_for_app_authorization(monkeypatch) -> None:
    monkeypatch.setattr(
        "ambilight_tuya.webapp.app.load_tuya_credentials",
        lambda: TuyaCredentials(
            access_id="id",
            access_key="key",
            api_endpoint="https://example.com",
            auth_scheme="app",
            app_identifier="com.sebastianprietoa.ambilight.localhost",
        ),
    )
    app = create_app()
    client = app.test_client()

    response = client.post("/api/list-devices", json={})

    assert response.status_code == 400
    payload = response.get_json()
    assert "OAuth 2.0 user authorization" in payload["error"]

    debug_response = client.get("/api/debug/logs")
    debug_payload = debug_response.get_json()
    assert any(entry["event"] == "tuya.oauth.required" for entry in debug_payload["entries"])
