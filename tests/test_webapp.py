from __future__ import annotations

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
