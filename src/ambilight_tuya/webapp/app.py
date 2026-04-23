from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime
import os
import re
import threading
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from ambilight_tuya.color_extractor import ColorExtractor, ScreenGridPreviewExtractor
from ambilight_tuya.config import ConfigError, load_hue_credentials, load_project_config, load_tuya_credentials
from ambilight_tuya.config.state_store import DashboardStateStore
from ambilight_tuya.device_mapper import DeviceMapper
from ambilight_tuya.hue_client import HueApiError, HueClient
from ambilight_tuya.models import AppConfig, RGBColor
from ambilight_tuya.screen_capture import ScreenCaptureService, list_monitors
from ambilight_tuya.sync_engine import AmbilightSyncEngine
from ambilight_tuya.tuya_client import TuyaClient, TuyaApiError
from ambilight_tuya.utils import configure_logging

POWER_CODES = ("switch_led", "switch", "switch_1")
BRIGHTNESS_CODES = ("bright_value_v2", "bright_value", "bright_value_1", "bright_value_2")
RGB_STATUS_CODES = ("work_mode", "colour_data", "colour_data_v2", "bright_value_v2", "temp_value_v2")
CATEGORY_LABELS = {
    "dj": "RGB light",
    "dd": "Light strip",
    "kg": "Switch",
    "cz": "Socket",
    "pc": "Power strip",
    "fs": "Fan",
    "kt": "Air conditioner",
    "qn": "Heater",
    "wk": "Thermostat",
}
PROVIDER_LABELS = {
    "tuya": "Tuya",
    "hue": "Hue",
}
HUE_CATEGORY_HINTS = {
    "Color light": ("light", "Hue color light", True),
    "Extended color light": ("light", "Hue color light", True),
    "Dimmable light": ("light", "Hue dimmable light", False),
    "On/Off plug-in unit": ("socket", "Hue smart plug", False),
    "On/Off light": ("light", "Hue light", False),
}


class SyncSession:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            "running": False,
            "dry_run": True,
            "duration": None,
            "monitor_index": None,
            "last_error": None,
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            alive = self._thread is not None and self._thread.is_alive()
            state = dict(self._state)
            state["running"] = alive
            return state

    def start(
        self,
        app_config: AppConfig,
        tuya_client: TuyaClient | None,
        dry_run: bool,
        duration: float | None,
    ) -> dict[str, Any]:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("Sync loop is already running")

            stop_event = threading.Event()
            self._stop_event = stop_event
            self._state = {
                "running": True,
                "dry_run": dry_run,
                "duration": duration,
                "monitor_index": app_config.capture.monitor_index,
                "last_error": None,
            }

            def runner() -> None:
                try:
                    engine = AmbilightSyncEngine(app_config, tuya_client)
                    engine.run(duration_seconds=duration, dry_run=dry_run, stop_event=stop_event)
                except Exception as exc:  # pragma: no cover
                    with self._lock:
                        self._state["last_error"] = str(exc)
                finally:
                    with self._lock:
                        self._state["running"] = False

            self._thread = threading.Thread(target=runner, daemon=True, name="ambilight-sync")
            self._thread.start()
            return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if self._stop_event is not None:
                self._stop_event.set()
            return self.status()


class OAuthSession:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token_response: dict[str, Any] | None = None
        self._last_code: str | None = None
        self._last_error: str | None = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "authorized": self._token_response is not None,
                "last_code": self._last_code,
                "last_error": self._last_error,
            }

    def set_token_response(self, code: str, token_response: dict[str, Any]) -> None:
        with self._lock:
            self._last_code = code
            self._token_response = token_response
            self._last_error = None

    def get_token_response(self) -> dict[str, Any] | None:
        with self._lock:
            return self._token_response

    def set_error(self, error_message: str) -> None:
        with self._lock:
            self._last_error = error_message

    def clear(self) -> None:
        with self._lock:
            self._token_response = None
            self._last_code = None
            self._last_error = None


class DebugLogSession:
    def __init__(self, limit: int = 60) -> None:
        self._lock = threading.Lock()
        self._entries: list[dict[str, Any]] = []
        self._limit = limit

    def add(self, event: str, details: dict[str, Any]) -> None:
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event,
            "details": details,
        }
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self._limit:
                self._entries = self._entries[-self._limit :]

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


class PreviewGridSession:
    def __init__(self, rows: int = 4, cols: int = 4) -> None:
        self.rows = rows
        self.cols = cols
        self._extractor = ScreenGridPreviewExtractor(rows=rows, cols=cols)
        self._lock = threading.Lock()
        self._last_colors: dict[str, RGBColor] = {}
        self._last_sampled_at: str | None = None

    def sample(self, frame, smoothing_config) -> dict[str, Any]:
        with self._lock:
            cells = []
            for cell in self._extractor.extract(frame):
                key = f"r{cell.row}c{cell.col}"
                previous = self._last_colors.get(key)
                if previous is None:
                    smoothed = cell.rgb
                elif cell.rgb.distance(previous) < smoothing_config.min_color_delta:
                    smoothed = previous
                else:
                    smoothed = previous.blend(cell.rgb, smoothing_config.alpha)
                self._last_colors[key] = smoothed
                cells.append(
                    {
                        "index": cell.index,
                        "row": cell.row,
                        "col": cell.col,
                        "rgb": list(smoothed.as_tuple()),
                        "hex": f"#{smoothed.r:02x}{smoothed.g:02x}{smoothed.b:02x}",
                    }
                )
            self._last_sampled_at = datetime.now(UTC).isoformat()
            return {
                "rows": self.rows,
                "cols": self.cols,
                "cells": cells,
                "sampled_at": self._last_sampled_at,
            }


def _resolve_preview_monitor_index(requested_monitor_index: str | None, fallback_monitor_index: int) -> int:
    if requested_monitor_index is None or not requested_monitor_index.strip():
        return fallback_monitor_index
    return int(requested_monitor_index.strip())


def _parse_rgb(raw_value: str) -> RGBColor:
    parts = [int(part.strip()) for part in raw_value.split(",")]
    if len(parts) != 3:
        raise ValueError("RGB value must be formatted as r,g,b")
    return RGBColor(*parts)


def _serialize_samples(samples: dict[str, Any]) -> dict[str, Any]:
    return {
        zone_name: {
            "rgb": sample.rgb.as_tuple(),
            "hsv": sample.hsv.as_dict(),
        }
        for zone_name, sample in samples.items()
    }


def _short_device_id(device_id: str) -> str:
    if len(device_id) <= 10:
        return device_id
    return f"{device_id[:6]}...{device_id[-4:]}"


def _normalize_provider(raw_provider: str | None) -> str:
    provider = str(raw_provider or "tuya").strip().lower()
    return provider if provider in {"tuya", "hue"} else "tuya"


def _device_key(provider: str, device_id: str) -> str:
    return f"{provider}:{device_id}"


def _split_device_key(device_key: str) -> tuple[str, str]:
    if ":" not in device_key:
        return "tuya", device_key
    provider, device_id = device_key.split(":", 1)
    return _normalize_provider(provider), device_id


def _status_items_to_map(status_items: list[dict[str, Any]] | None) -> dict[str, Any]:
    if not status_items:
        return {}
    return {
        item.get("code"): item.get("value")
        for item in status_items
        if item.get("code")
    }


def _derive_power_state(status_map: dict[str, Any]) -> str:
    for code in POWER_CODES:
        if code in status_map:
            return "on" if bool(status_map[code]) else "off"
    return "unknown"


def _guess_rgb_capability(raw_device: dict[str, Any], status_map: dict[str, Any]) -> bool:
    codes = set(status_map.keys())
    if any(code in codes for code in RGB_STATUS_CODES):
        return True
    category = str(raw_device.get("category", "")).lower()
    name = str(raw_device.get("name", "")).lower()
    product_name = str(raw_device.get("product_name", "")).lower()
    rgb_hints = ("rgb", "colour", "color", "bulb", "light", "strip", "luz")
    if category in {"dj", "dd"}:
        return True
    return any(hint in f"{name} {product_name}" for hint in rgb_hints)


def _extract_room_name(raw_device: dict[str, Any]) -> str | None:
    direct_candidates = (
        raw_device.get("room_name"),
        raw_device.get("room"),
        raw_device.get("space_name"),
        raw_device.get("space"),
        raw_device.get("home_name"),
        raw_device.get("home"),
    )
    for candidate in direct_candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    local_key_candidates = (
        raw_device.get("local_key"),
        raw_device.get("custom_name"),
    )
    for candidate in local_key_candidates:
        if isinstance(candidate, dict):
            for nested_key in ("room_name", "space_name", "home_name"):
                nested_value = candidate.get(nested_key)
                if isinstance(nested_value, str) and nested_value.strip():
                    return nested_value.strip()
    return None


def _extract_hue_room_name(raw_device: dict[str, Any]) -> str | None:
    room = raw_device.get("room")
    if isinstance(room, str) and room.strip():
        return room.strip()
    return None


def _current_brightness_percent(
    status_map: dict[str, Any],
    min_value: int | None = None,
    max_value: int | None = None,
) -> int | None:
    for code in BRIGHTNESS_CODES:
        if code not in status_map:
            continue
        try:
            raw_value = int(status_map[code])
        except (TypeError, ValueError):
            return None
        resolved_min = min_value if min_value is not None else (10 if code.endswith("_v2") else 0)
        resolved_max = max_value if max_value is not None else (1000 if code.endswith("_v2") else 255)
        if resolved_max <= resolved_min:
            return 100
        normalized = round(((raw_value - resolved_min) / (resolved_max - resolved_min)) * 100)
        return max(0, min(normalized, 100))
    return None


def _capability_snapshot(
    raw_device: dict[str, Any],
    status_map: dict[str, Any],
    capabilities: dict[str, Any] | None = None,
    provider: str = "tuya",
) -> dict[str, Any]:
    resolved_capabilities = capabilities or {}
    power_codes = list(resolved_capabilities.get("power_codes", []))
    brightness_supported = bool(resolved_capabilities.get("brightness_supported"))
    brightness_min = resolved_capabilities.get("brightness_min")
    brightness_max = resolved_capabilities.get("brightness_max")
    is_rgb_capable = bool(resolved_capabilities.get("color_supported"))
    color_supported = bool(resolved_capabilities.get("color_supported"))
    brightness_percent = None

    if provider == "tuya":
        if not power_codes:
            power_codes = [code for code in POWER_CODES if code in status_map]
        if not brightness_supported:
            brightness_supported = any(code in status_map for code in BRIGHTNESS_CODES)
        brightness_percent = _current_brightness_percent(status_map, brightness_min, brightness_max)
        is_rgb_capable = _guess_rgb_capability(raw_device, status_map)
        if is_rgb_capable:
            brightness_supported = True
        color_supported = bool(resolved_capabilities.get("color_supported")) or is_rgb_capable
    else:
        if not power_codes and resolved_capabilities.get("power_supported"):
            power_codes = ["on"]
        if brightness_supported:
            try:
                current_brightness = int(resolved_capabilities.get("current_brightness"))
            except (TypeError, ValueError):
                current_brightness = None
            if current_brightness is not None:
                brightness_percent = round((current_brightness / 254) * 100)
        type_hint = str(raw_device.get("type", "")).strip()
        if type_hint in HUE_CATEGORY_HINTS:
            is_rgb_capable = HUE_CATEGORY_HINTS[type_hint][2]
            color_supported = HUE_CATEGORY_HINTS[type_hint][2]

    return {
        "power_supported": bool(power_codes),
        "power_codes": power_codes,
        "brightness_supported": brightness_supported,
        "brightness_code": resolved_capabilities.get("brightness_code"),
        "brightness_min": brightness_min,
        "brightness_max": brightness_max,
        "current_brightness": brightness_percent,
        "current_brightness_raw": resolved_capabilities.get("current_brightness"),
        "is_rgb_capable": is_rgb_capable,
        "supports_color": color_supported,
        "color_mode_code": resolved_capabilities.get("color_mode_code"),
        "color_data_code": resolved_capabilities.get("color_data_code"),
    }


def _normalize_device_record(
    raw_device: dict[str, Any],
    status_map: dict[str, Any] | None = None,
    capabilities: dict[str, Any] | None = None,
    room: str | None = None,
    provider: str = "tuya",
) -> dict[str, Any]:
    resolved_status_map = status_map or _status_items_to_map(raw_device.get("status"))
    category = str(raw_device.get("category", "")).lower()
    type_hint = str(raw_device.get("type", "")).strip()
    name = (
        str(raw_device.get("name") or raw_device.get("custom_name") or "").strip()
        or f"Device {_short_device_id(str(raw_device.get('id', 'unknown')))}"
    )
    online_value = raw_device.get("online")
    if online_value is None and "online" in resolved_status_map:
        online_value = resolved_status_map.get("online")
    if isinstance(online_value, bool):
        online = online_value
    else:
        online = None
    power_state = _derive_power_state(resolved_status_map)
    if provider == "hue":
        if online is None:
            online = bool(resolved_status_map.get("reachable", True))
        power_state = "on" if bool(resolved_status_map.get("on")) else "off"
    capability_snapshot = _capability_snapshot(raw_device, resolved_status_map, capabilities, provider=provider)
    product_name = str(raw_device.get("product_name") or "").strip()
    if provider == "hue":
        hint_category, hint_label, _ = HUE_CATEGORY_HINTS.get(type_hint, ("light", type_hint or "Hue light", False))
        category = category or hint_category
        product_name = product_name or str(raw_device.get("modelid") or "").strip()
        resolved_room = room or _extract_hue_room_name(raw_device)
        type_label = hint_label
    else:
        resolved_room = room or _extract_room_name(raw_device)
        type_label = CATEGORY_LABELS.get(category, product_name or "Tuya device")
    return {
        "id": str(raw_device.get("id", "")).strip(),
        "provider": provider,
        "provider_label": PROVIDER_LABELS.get(provider, provider.title()),
        "device_key": _device_key(provider, str(raw_device.get("id", "")).strip()),
        "short_id": _short_device_id(str(raw_device.get("id", "")).strip()),
        "name": name,
        "category": category or "unknown",
        "type_label": type_label,
        "product_name": product_name,
        "online": online,
        "reachability_label": "Online" if online is True else "Offline" if online is False else "Unknown",
        "power_state": power_state,
        "state_label": power_state.title(),
        "room": resolved_room,
        "status_map": resolved_status_map,
        "raw": raw_device,
        **capability_snapshot,
    }


def _serialize_device_status(status, capabilities: dict[str, Any] | None = None, provider: str = "tuya") -> dict[str, Any]:
    payload = asdict(status)
    status_map = dict(status.raw.get("status_map", {}))
    payload["status_map"] = status_map
    payload["power_state"] = status.raw.get("power_state", _derive_power_state(status_map))
    payload["reachability_label"] = "Online" if status.online else "Offline"
    payload["state_label"] = payload["power_state"].title()
    payload["provider"] = provider
    payload["provider_label"] = PROVIDER_LABELS.get(provider, provider.title())
    payload["device_key"] = _device_key(provider, status.device_id)
    payload.update(_capability_snapshot({"id": status.device_id}, status_map, capabilities, provider=provider))
    return payload


def _friendly_tuya_message(error_message: str, default_message: str) -> str:
    if "code': 1106" in error_message or '"code": 1106' in error_message:
        return "Tuya no permite discovery de este usuario/proyecto. Puedes seguir controlando dispositivos guardados o seleccionar uno conocido."
    if "code': 2008" in error_message or '"code": 2008' in error_message:
        attempted_codes = re.search(r"Attempted datapoints: ([^.]*)", error_message)
        if attempted_codes:
            return (
                "Este dispositivo no acepto el perfil de energia esperado. "
                f"Se probaron: {attempted_codes.group(1)}."
            )
        return "Este dispositivo no soporta el comando solicitado con su datapoint actual."
    if "does not expose a supported power switch datapoint" in error_message:
        return "Este dispositivo no expone un switch compatible para encendido/apagado desde esta integracion."
    if "does not expose a supported brightness datapoint" in error_message:
        return "Este dispositivo no ofrece control de brillo."
    if "does not expose color datapoints" in error_message:
        return "Este dispositivo no soporta color."
    return default_message


def _friendly_hue_message(error_message: str, default_message: str) -> str:
    text = str(error_message)
    if "unauthorized user" in text.lower():
        return "La Hue Bridge rechazo la application key configurada."
    if "not reachable" in text.lower():
        return "La luz Hue aparece no alcanzable desde el bridge."
    return default_message


def _friendly_provider_message(provider: str, error_message: str, default_message: str) -> str:
    if provider == "hue":
        return _friendly_hue_message(error_message, default_message)
    return _friendly_tuya_message(error_message, default_message)


def _fetch_capabilities_safe(client: TuyaClient, device_id: str, status=None) -> dict[str, Any]:
    try:
        return client.get_device_capabilities(device_id, status=status)
    except Exception:
        return {}


def create_app() -> Flask:
    load_dotenv()
    configure_logging()
    app = Flask(__name__, template_folder="templates", static_folder="static")
    sync_session = SyncSession()
    oauth_session = OAuthSession()
    debug_log_session = DebugLogSession()
    preview_session = PreviewGridSession(rows=4, cols=4)
    state_store = DashboardStateStore()

    def _record_debug(event: str, details: dict[str, Any]) -> None:
        debug_log_session.add(event, details)

    def _oauth_callback_url() -> str:
        return os.getenv(
            "TUYA_OAUTH_CALLBACK_URL",
            "http://127.0.0.1:8787/api/tuya/oauth/callback",
        )

    def _get_tuya_client(prefer_user_oauth: bool = False) -> TuyaClient:
        credentials = load_tuya_credentials()
        client = TuyaClient(credentials)
        _record_debug("tuya.client.created", {"tuya": client.debug_snapshot()})
        token_response = oauth_session.get_token_response()
        if token_response is not None:
            client.restore_token_response(token_response)
            _record_debug("tuya.oauth.token_restored", {"tuya": client.debug_snapshot()})
            return client
        if prefer_user_oauth and credentials.auth_scheme in {"app", "auto"}:
            _record_debug(
                "tuya.oauth.required",
                {
                    "callback_url": _oauth_callback_url(),
                    "tuya": client.debug_snapshot(),
                },
            )
            raise TuyaApiError(
                "No active Tuya OAuth 2.0 user authorization. In Tuya Platform go to Devices > Link App Account > Configure OAuth 2.0 Authorization and set the callback URL to "
                f"{_oauth_callback_url()}"
            )
        return client

    def _get_hue_client(required: bool = False) -> HueClient | None:
        credentials = load_hue_credentials()
        if credentials is None:
            if required:
                raise HueApiError("Hue Bridge is not configured. Add HUE_BRIDGE_IP and HUE_APPLICATION_KEY to .env.")
            return None
        client = HueClient(credentials)
        _record_debug("hue.client.created", {"hue": client.debug_snapshot()})
        return client

    def _resolve_provider_device(payload: dict[str, Any]) -> tuple[str, str]:
        raw_device_key = str(payload.get("device_key", "")).strip()
        if raw_device_key:
            provider, device_id = _split_device_key(raw_device_key)
            if device_id:
                return provider, device_id
        provider = _normalize_provider(payload.get("provider"))
        device_id = str(payload.get("device_id", "")).strip()
        return provider, device_id

    def _list_hue_devices() -> list[dict[str, Any]]:
        client = _get_hue_client(required=False)
        if client is None:
            return []
        lights = client.list_lights()
        devices: list[dict[str, Any]] = []
        for light in lights:
            capabilities = client.get_light_capabilities(light["id"], light=light)
            devices.append(
                _normalize_device_record(
                    {
                        "id": light["id"],
                        "name": light["name"],
                        "type": light.get("type"),
                        "product_name": light.get("productname", ""),
                        "modelid": light.get("modelid", ""),
                        "room": light.get("room"),
                        "online": bool(light.get("state", {}).get("reachable", True)),
                    },
                    status_map=dict(light.get("state", {})),
                    capabilities=capabilities,
                    room=light.get("room"),
                    provider="hue",
                )
            )
        return devices

    def _fetch_unified_devices() -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        devices: list[dict[str, Any]] = []
        warnings: list[dict[str, str]] = []

        try:
            tuya_client = _get_tuya_client(prefer_user_oauth=True)
            raw_devices = tuya_client.list_devices()
            devices.extend(_normalize_device_record(raw_device) for raw_device in raw_devices)
            _record_debug("tuya.list_devices.success", {"device_count": len(raw_devices), "tuya": tuya_client.debug_snapshot()})
        except Exception as exc:
            warnings.append({"provider": "tuya", "message": _friendly_tuya_message(str(exc), "No fue posible cargar dispositivos Tuya.")})
            _record_debug("tuya.list_devices.error", {"error": str(exc)})

        try:
            hue_devices = _list_hue_devices()
            if hue_devices:
                devices.extend(hue_devices)
                hue_client = _get_hue_client(required=False)
                if hue_client is not None:
                    _record_debug("hue.list_devices.success", {"device_count": len(hue_devices), "hue": hue_client.debug_snapshot()})
        except Exception as exc:
            warnings.append({"provider": "hue", "message": _friendly_hue_message(str(exc), "No fue posible cargar luces Hue.")})
            _record_debug("hue.list_devices.error", {"error": str(exc)})

        devices.sort(key=lambda item: (item["room"] or "zzz", item["name"].lower(), item["provider"], item["id"]))
        return devices, warnings

    def _apply_color_to_device(provider: str, device_id: str, color: RGBColor) -> dict[str, Any]:
        if provider == "hue":
            client = _get_hue_client(required=True)
            assert client is not None
            return client.set_fixed_color(device_id, color)
        app_config = load_project_config()
        client = _get_tuya_client(prefer_user_oauth=True)
        status = client.get_device_status(device_id)
        capabilities = _fetch_capabilities_safe(client, device_id, status=status)
        return client.set_fixed_color(
            device_id,
            color,
            app_config.command_profiles["default"],
            capabilities=capabilities,
        )

    @app.get("/")
    def index() -> str:
        return render_template("index.html")

    @app.get("/api/status")
    def api_status():
        monitors = list_monitors()
        primary_monitor = next((monitor for monitor in monitors if monitor.get("is_primary")), monitors[0] if monitors else None)
        hue_client = _get_hue_client(required=False)
        return jsonify(
            {
                "sync": sync_session.status(),
                "oauth": oauth_session.status(),
                "oauth_callback_url": _oauth_callback_url(),
                "debug_log_count": len(debug_log_session.list()),
                "ambilight_mapping_count": len(state_store.get_ambilight_mapping()),
                "hue": {
                    "configured": hue_client is not None,
                    "bridge_ip": hue_client.credentials.bridge_ip if hue_client is not None else None,
                },
                "preview": {
                    "rows": 4,
                    "cols": 4,
                    "target_fps": 8,
                    "apply_target_fps": 4,
                    "default_monitor_index": primary_monitor.get("index") if primary_monitor else 1,
                },
                "monitors": monitors,
            }
        )

    @app.get("/api/debug/logs")
    def api_debug_logs():
        return jsonify({"entries": debug_log_session.list()})

    @app.post("/api/debug/clear")
    def api_debug_clear():
        debug_log_session.clear()
        return jsonify({"entries": [], "cleared": True})

    @app.post("/api/list-devices")
    def api_list_devices():
        devices, warnings = _fetch_unified_devices()
        return jsonify(
            {
                "devices": devices,
                "count": len(devices),
                "warnings": warnings,
                "mapping": state_store.get_ambilight_mapping(),
            }
        )

    @app.post("/api/get-device-status")
    def api_get_device_status():
        payload = request.get_json(silent=True) or {}
        provider, device_id = _resolve_provider_device(payload)
        if not device_id:
            raise ValueError("device_id is required")
        if provider == "hue":
            client = _get_hue_client(required=True)
            assert client is not None
            try:
                status = client.get_light_status(device_id)
                capabilities = client.get_light_capabilities(device_id, light=status.raw.get("light"))
            except Exception as exc:
                _record_debug(
                    "hue.get_device_status.error",
                    {"device_id": device_id, "error": str(exc), "hue": client.debug_snapshot()},
                )
                raise ValueError(_friendly_hue_message(str(exc), "No fue posible consultar el estado de la luz Hue."))
            _record_debug(
                "hue.get_device_status.success",
                {"device_id": device_id, "hue": client.debug_snapshot()},
            )
            return jsonify(_serialize_device_status(status, capabilities, provider="hue"))

        client = _get_tuya_client(prefer_user_oauth=True)
        try:
            status = client.get_device_status(device_id)
            capabilities = _fetch_capabilities_safe(client, device_id, status=status)
        except Exception as exc:
            _record_debug(
                "tuya.get_device_status.error",
                {"device_id": device_id, "error": str(exc), "tuya": client.debug_snapshot()},
            )
            raise ValueError(_friendly_tuya_message(str(exc), "No fue posible consultar el estado del dispositivo."))
        _record_debug(
            "tuya.get_device_status.success",
            {"device_id": device_id, "tuya": client.debug_snapshot()},
        )
        return jsonify(_serialize_device_status(status, capabilities, provider="tuya"))

    @app.post("/api/set-fixed-color")
    def api_set_fixed_color():
        payload = request.get_json(silent=True) or {}
        provider, device_id = _resolve_provider_device(payload)
        zone = str(payload.get("zone", "")).strip()
        rgb = _parse_rgb(str(payload.get("rgb", "")).strip())
        if device_id:
            if provider == "hue":
                client = _get_hue_client(required=True)
                assert client is not None
                try:
                    result = client.set_fixed_color(device_id, rgb)
                except Exception as exc:
                    _record_debug(
                        "hue.set_fixed_color.error",
                        {
                            "device_id": device_id,
                            "rgb": rgb.as_tuple(),
                            "error": str(exc),
                            "hue": client.debug_snapshot(),
                        },
                    )
                    raise ValueError(_friendly_hue_message(str(exc), "No fue posible aplicar color a esta luz Hue."))
                _record_debug(
                    "hue.set_fixed_color.success",
                    {"device_id": device_id, "rgb": rgb.as_tuple(), "hue": client.debug_snapshot()},
                )
                return jsonify({"provider": "hue", "device_id": device_id, "result": result})

            app_config = load_project_config()
            client = _get_tuya_client(prefer_user_oauth=True)
            try:
                status = client.get_device_status(device_id)
                capabilities = _fetch_capabilities_safe(client, device_id, status=status)
                result = client.set_fixed_color(
                    device_id,
                    rgb,
                    app_config.command_profiles["default"],
                    capabilities=capabilities,
                )
            except Exception as exc:
                _record_debug(
                    "tuya.set_fixed_color.error",
                    {
                        "device_id": device_id,
                        "rgb": rgb.as_tuple(),
                        "error": str(exc),
                        "tuya": client.debug_snapshot(),
                    },
                )
                raise ValueError(_friendly_tuya_message(str(exc), "No fue posible aplicar color a este dispositivo."))
            _record_debug(
                "tuya.set_fixed_color.success",
                {"device_id": device_id, "rgb": rgb.as_tuple(), "tuya": client.debug_snapshot()},
            )
            return jsonify({"provider": "tuya", "device_id": device_id, "result": result})
        if not zone:
            raise ValueError("device_id or zone is required")
        app_config = load_project_config()
        client = _get_tuya_client(prefer_user_oauth=True)
        routing = DeviceMapper(app_config).resolve(zone)
        if routing is None or not routing.device_ids:
            raise ValueError(f"No devices configured for zone {zone}")
        results = []
        for resolved_device_id in routing.device_ids:
            try:
                result = client.set_fixed_color(resolved_device_id, rgb, routing.profile)
            except Exception as exc:
                _record_debug(
                    "tuya.set_fixed_color.error",
                    {
                        "device_id": resolved_device_id,
                        "zone": zone,
                        "rgb": rgb.as_tuple(),
                        "error": str(exc),
                        "tuya": client.debug_snapshot(),
                    },
                )
                raise
            results.append({"device_id": resolved_device_id, "result": result})
        _record_debug(
            "tuya.set_fixed_color.success",
            {"zone": zone, "device_count": len(results), "rgb": rgb.as_tuple(), "tuya": client.debug_snapshot()},
        )
        return jsonify({"zone": zone, "results": results})

    @app.post("/api/set-power")
    def api_set_power():
        payload = request.get_json(silent=True) or {}
        provider, device_id = _resolve_provider_device(payload)
        zone = str(payload.get("zone", "")).strip()
        state_raw = str(payload.get("state", "")).strip().lower()
        if state_raw not in {"on", "off"}:
            raise ValueError("state must be 'on' or 'off'")
        is_on = state_raw == "on"
        if device_id:
            if provider == "hue":
                client = _get_hue_client(required=True)
                assert client is not None
                try:
                    result = client.set_power_state(device_id, is_on)
                except Exception as exc:
                    _record_debug(
                        "hue.set_power.error",
                        {"device_id": device_id, "state": state_raw, "error": str(exc), "hue": client.debug_snapshot()},
                    )
                    raise ValueError(_friendly_hue_message(str(exc), "No fue posible cambiar el estado de energia en Hue."))
                _record_debug(
                    "hue.set_power.success",
                    {"device_id": device_id, "state": state_raw, "hue": client.debug_snapshot()},
                )
                return jsonify({"provider": "hue", "device_id": device_id, "state": state_raw, "result": result})

            app_config = load_project_config()
            client = _get_tuya_client(prefer_user_oauth=True)
            try:
                status = client.get_device_status(device_id)
                capabilities = _fetch_capabilities_safe(client, device_id, status=status)
                result = client.set_power_state(
                    device_id,
                    is_on,
                    app_config.command_profiles["default"],
                    capabilities=capabilities,
                )
            except Exception as exc:
                _record_debug(
                    "tuya.set_power.error",
                    {"device_id": device_id, "state": state_raw, "error": str(exc), "tuya": client.debug_snapshot()},
                )
                raise ValueError(_friendly_tuya_message(str(exc), "No fue posible cambiar el estado de energia."))
            _record_debug(
                "tuya.set_power.success",
                {"device_id": device_id, "state": state_raw, "tuya": client.debug_snapshot()},
            )
            return jsonify({"provider": "tuya", "device_id": device_id, "state": state_raw, "result": result})
        if not zone:
            raise ValueError("device_id or zone is required")
        app_config = load_project_config()
        client = _get_tuya_client(prefer_user_oauth=True)
        routing = DeviceMapper(app_config).resolve(zone)
        if routing is None or not routing.device_ids:
            raise ValueError(f"No devices configured for zone {zone}")
        results = []
        for resolved_device_id in routing.device_ids:
            try:
                result = client.set_power_state(resolved_device_id, is_on, routing.profile)
            except Exception as exc:
                _record_debug(
                    "tuya.set_power.error",
                    {
                        "device_id": resolved_device_id,
                        "zone": zone,
                        "state": state_raw,
                        "error": str(exc),
                        "tuya": client.debug_snapshot(),
                    },
                )
                raise
            results.append({"device_id": resolved_device_id, "result": result})
        _record_debug(
            "tuya.set_power.success",
            {"zone": zone, "state": state_raw, "device_count": len(results), "tuya": client.debug_snapshot()},
        )
        return jsonify({"zone": zone, "state": state_raw, "results": results})

    @app.post("/api/set-brightness")
    def api_set_brightness():
        payload = request.get_json(silent=True) or {}
        provider, device_id = _resolve_provider_device(payload)
        if not device_id:
            raise ValueError("device_id is required")
        try:
            level = int(payload.get("level"))
        except (TypeError, ValueError):
            raise ValueError("level must be an integer between 0 and 100")
        level = max(0, min(level, 100))
        if provider == "hue":
            client = _get_hue_client(required=True)
            assert client is not None
            try:
                result = client.set_brightness(device_id, level)
            except Exception as exc:
                _record_debug(
                    "hue.set_brightness.error",
                    {"device_id": device_id, "level": level, "error": str(exc), "hue": client.debug_snapshot()},
                )
                raise ValueError(_friendly_hue_message(str(exc), "No fue posible ajustar el brillo en Hue."))
            _record_debug(
                "hue.set_brightness.success",
                {"device_id": device_id, "level": level, "result": result, "hue": client.debug_snapshot()},
            )
            return jsonify({"provider": "hue", "device_id": device_id, "level": level, "result": result})

        client = _get_tuya_client(prefer_user_oauth=True)
        try:
            status = client.get_device_status(device_id)
            capabilities = _fetch_capabilities_safe(client, device_id, status=status)
            result = client.set_brightness(device_id, level, capabilities=capabilities)
        except Exception as exc:
            _record_debug(
                "tuya.set_brightness.error",
                {"device_id": device_id, "level": level, "error": str(exc), "tuya": client.debug_snapshot()},
            )
            raise ValueError(_friendly_tuya_message(str(exc), "No fue posible ajustar el brillo."))
        _record_debug(
            "tuya.set_brightness.success",
            {"device_id": device_id, "level": level, "result": result, "tuya": client.debug_snapshot()},
        )
        return jsonify({"provider": "tuya", "device_id": device_id, "level": level, "result": result})

    @app.post("/api/screen-sample")
    def api_screen_sample():
        payload = request.get_json(silent=True) or {}
        app_config = load_project_config()
        monitor_index = payload.get("monitor_index")
        if monitor_index is not None:
            app_config = replace(
                app_config,
                capture=replace(app_config.capture, monitor_index=int(monitor_index)),
            )
        capture = ScreenCaptureService(app_config.capture)
        frame = capture.capture_frame()
        samples = ColorExtractor(app_config.extraction).extract(frame)
        return jsonify(
            {
                "monitors": list_monitors(),
                "frame_shape": list(frame.shape),
                "samples": _serialize_samples(samples),
            }
        )

    @app.get("/api/ambilight-preview")
    def api_ambilight_preview():
        app_config = load_project_config()
        monitors = list_monitors()
        primary_monitor = next((monitor for monitor in monitors if monitor.get("is_primary")), monitors[0] if monitors else None)
        fallback_monitor_index = primary_monitor.get("index") if primary_monitor else app_config.capture.monitor_index
        monitor_index = _resolve_preview_monitor_index(request.args.get("monitor_index"), fallback_monitor_index)
        capture_config = replace(app_config.capture, monitor_index=monitor_index)
        capture = ScreenCaptureService(capture_config)
        try:
            frame, monitor_metadata = capture.capture_frame_with_metadata()
            payload = preview_session.sample(frame, app_config.smoothing)
        except Exception as exc:
            _record_debug(
                "preview.grid.error",
                {"monitor_index": monitor_index, "error": str(exc)},
            )
            raise ValueError("No fue posible capturar la vista previa de pantalla.")

        _record_debug(
            "preview.grid.success",
            {
                "monitor_index": monitor_index,
                "monitor": monitor_metadata,
                "frame_shape": list(frame.shape),
            },
        )
        payload.update(
            {
                "monitor_index": monitor_index,
                "source_monitor": monitor_metadata,
                "is_primary_monitor": bool(monitor_metadata.get("is_primary")),
                "frame_shape": list(frame.shape),
                "capture_origin": {
                    "left": monitor_metadata.get("left"),
                    "top": monitor_metadata.get("top"),
                    "width": monitor_metadata.get("width"),
                    "height": monitor_metadata.get("height"),
                },
                "mapping": state_store.get_ambilight_mapping(),
                "monitors": monitors,
            }
        )
        return jsonify(payload)

    @app.get("/api/ambilight-mapping")
    def api_get_ambilight_mapping():
        devices, warnings = _fetch_unified_devices()
        color_devices = [
            {
                "device_key": device["device_key"],
                "provider": device["provider"],
                "device_id": device["id"],
                "name": device["name"],
                "provider_label": device["provider_label"],
                "room": device.get("room"),
                "type_label": device["type_label"],
                "online": device.get("online"),
            }
            for device in devices
            if device.get("supports_color") or device.get("is_rgb_capable")
        ]
        return jsonify(
            {
                "mapping": state_store.get_ambilight_mapping(),
                "devices": color_devices,
                "warnings": warnings,
            }
        )

    @app.post("/api/ambilight-mapping")
    def api_set_ambilight_mapping():
        payload = request.get_json(silent=True) or {}
        raw_mapping = payload.get("mapping", {})
        if not isinstance(raw_mapping, dict):
            raise ValueError("mapping must be an object keyed by cell id")
        cleaned = state_store.save_ambilight_mapping(raw_mapping)
        _record_debug("preview.mapping.saved", {"mapping_count": len(cleaned)})
        return jsonify({"mapping": cleaned, "count": len(cleaned)})

    @app.post("/api/ambilight/apply-preview-frame")
    def api_apply_preview_frame():
        payload = request.get_json(silent=True) or {}
        mapping = state_store.get_ambilight_mapping()
        if not mapping:
            raise ValueError("No hay celdas mapeadas para aplicar.")

        cells = payload.get("cells")
        if not isinstance(cells, list) or not cells:
            preview_payload = api_ambilight_preview().get_json()
            cells = preview_payload.get("cells", [])

        cells_by_key = {
            f"r{cell.get('row')}c{cell.get('col')}": cell
            for cell in cells
            if isinstance(cell, dict)
        }
        applied: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for cell_key, target in mapping.items():
            cell = cells_by_key.get(cell_key)
            if cell is None:
                skipped.append({"cell": cell_key, "reason": "cell_not_present"})
                continue
            provider = _normalize_provider(target.get("provider"))
            device_id = str(target.get("device_id", "")).strip()
            if not device_id:
                skipped.append({"cell": cell_key, "reason": "device_missing"})
                continue
            try:
                color = RGBColor(*cell.get("rgb", [0, 0, 0]))
                result = _apply_color_to_device(provider, device_id, color)
            except Exception as exc:
                skipped.append(
                    {
                        "cell": cell_key,
                        "provider": provider,
                        "device_id": device_id,
                        "reason": _friendly_provider_message(provider, str(exc), "No fue posible aplicar color."),
                    }
                )
                continue
            applied.append(
                {
                    "cell": cell_key,
                    "provider": provider,
                    "device_id": device_id,
                    "rgb": cell.get("rgb"),
                    "hex": cell.get("hex"),
                    "result": result,
                }
            )
        _record_debug(
            "preview.frame.applied",
            {"applied_count": len(applied), "skipped_count": len(skipped)},
        )
        return jsonify(
            {
                "applied": applied,
                "skipped": skipped,
                "mapping_count": len(mapping),
            }
        )

    @app.post("/api/sync/start")
    def api_sync_start():
        payload = request.get_json(silent=True) or {}
        dry_run = bool(payload.get("dry_run", True))
        duration_raw = payload.get("duration")
        duration = float(duration_raw) if duration_raw not in (None, "", "null") else None
        monitor_index = payload.get("monitor_index")
        app_config = load_project_config()
        tuya_client = None if dry_run else _get_tuya_client(prefer_user_oauth=True)
        if monitor_index is not None:
            app_config = replace(
                app_config,
                capture=replace(app_config.capture, monitor_index=int(monitor_index)),
            )
        return jsonify(sync_session.start(app_config, tuya_client, dry_run=dry_run, duration=duration))

    @app.post("/api/sync/stop")
    def api_sync_stop():
        return jsonify(sync_session.stop())

    @app.get("/api/tuya/oauth/config")
    def api_oauth_config():
        return jsonify(
            {
                "callback_url": _oauth_callback_url(),
                "status": oauth_session.status(),
                "message": "Configure this callback URL in Tuya Platform > Devices > Link App Account > Configure OAuth 2.0 Authorization.",
            }
        )

    @app.get("/api/tuya/oauth/callback")
    def api_oauth_callback():
        code = request.args.get("code", "").strip()
        error = request.args.get("error", "").strip()
        error_description = request.args.get("error_description", "").strip()
        if error:
            message = error_description or error
            oauth_session.set_error(message)
            return (
                f"<h1>Tuya OAuth error</h1><p>{message}</p><p>Return to the dashboard and try again.</p>",
                400,
                {"Content-Type": "text/html; charset=utf-8"},
            )
        if not code:
            oauth_session.set_error("Missing OAuth authorization code in callback request")
            return (
                "<h1>Missing Tuya OAuth code</h1><p>The callback did not include a code.</p>",
                400,
                {"Content-Type": "text/html; charset=utf-8"},
            )

        client = _get_tuya_client(prefer_user_oauth=False)
        try:
            token_response = client.connect_with_authorization_code(code)
        except Exception as exc:
            _record_debug(
                "tuya.oauth.callback.error",
                {"error": str(exc), "tuya": client.debug_snapshot()},
            )
            raise
        oauth_session.set_token_response(code, token_response)
        _record_debug(
            "tuya.oauth.callback.success",
            {"tuya": client.debug_snapshot()},
        )
        return (
            "<h1>Tuya OAuth connected</h1><p>User authorization was stored successfully. Return to the dashboard and refresh status.</p>",
            200,
            {"Content-Type": "text/html; charset=utf-8"},
        )

    @app.errorhandler(ConfigError)
    @app.errorhandler(HueApiError)
    @app.errorhandler(TuyaApiError)
    @app.errorhandler(ValueError)
    @app.errorhandler(RuntimeError)
    def handle_expected_error(error: Exception):
        return jsonify({"error": str(error)}), 400

    @app.errorhandler(Exception)
    def handle_unexpected_error(error: Exception):  # pragma: no cover
        return jsonify({"error": f"Unexpected server error: {error}"}), 500

    return app
