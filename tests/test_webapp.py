from __future__ import annotations

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
