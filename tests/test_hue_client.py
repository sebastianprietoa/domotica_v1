from __future__ import annotations

from ambilight_tuya.hue_client import HueClient
from ambilight_tuya.models import HueCredentials, RGBColor


class FakeResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self) -> None:
        self.verify = False
        self.headers = {}
        self.requests = []

    def request(self, method: str, url: str, json=None, timeout: int = 5):
        self.requests.append((method, url, json))
        if method == "GET" and url.endswith("/api/app-key/"):
            return FakeResponse(
                {
                    "lights": {
                        "1": {
                            "name": "Hue Iris",
                            "type": "Extended color light",
                            "modelid": "LCT001",
                            "productname": "Hue color lamp",
                            "state": {"on": True, "reachable": True, "bri": 200, "hue": 5000, "sat": 140},
                        }
                    },
                    "groups": {
                        "10": {"name": "Living Room", "type": "Room", "lights": ["1"]},
                    },
                }
            )
        if method == "GET" and url.endswith("/api/app-key/lights/1"):
            return FakeResponse(
                {
                    "name": "Hue Iris",
                    "type": "Extended color light",
                    "state": {"on": True, "reachable": True, "bri": 200, "hue": 5000, "sat": 140},
                }
            )
        return FakeResponse([{"success": {"/lights/1/state/on": True}}])


def test_hue_client_lists_lights_with_room_metadata() -> None:
    client = HueClient(
        HueCredentials(bridge_ip="192.168.1.10", application_key="app-key"),
        session_factory=FakeSession,
    )

    lights = client.list_lights()

    assert len(lights) == 1
    assert lights[0]["id"] == "1"
    assert lights[0]["room"] == "Living Room"


def test_hue_client_sets_brightness_without_color_fields() -> None:
    client = HueClient(
        HueCredentials(bridge_ip="192.168.1.10", application_key="app-key"),
        session_factory=FakeSession,
    )

    result = client.set_brightness("1", 50)

    assert result["strategy"] == "hue_bri_only"
    method, url, payload = client._session.requests[-1]
    assert method == "PUT"
    assert url.endswith("/api/app-key/lights/1/state")
    assert "bri" in payload
    assert "hue" not in payload
    assert "sat" not in payload


def test_hue_client_sets_color_with_hue_payload() -> None:
    client = HueClient(
        HueCredentials(bridge_ip="192.168.1.10", application_key="app-key"),
        session_factory=FakeSession,
    )

    client.set_fixed_color("1", RGBColor(255, 0, 0))

    _, _, payload = client._session.requests[-1]
    assert payload["on"] is True
    assert "hue" in payload
    assert "sat" in payload
