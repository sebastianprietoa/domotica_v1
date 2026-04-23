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
from ambilight_tuya.config import ConfigError, load_app_config, load_project_config, load_tuya_credentials
from ambilight_tuya.device_mapper import DeviceMapper
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
) -> dict[str, Any]:
    resolved_capabilities = capabilities or {}
    power_codes = list(resolved_capabilities.get("power_codes", []))
    if not power_codes:
        power_codes = [code for code in POWER_CODES if code in status_map]
    brightness_supported = bool(resolved_capabilities.get("brightness_supported"))
    if not brightness_supported:
        brightness_supported = any(code in status_map for code in BRIGHTNESS_CODES)
    brightness_min = resolved_capabilities.get("brightness_min")
    brightness_max = resolved_capabilities.get("brightness_max")
    brightness_percent = _current_brightness_percent(status_map, brightness_min, brightness_max)
    is_rgb_capable = _guess_rgb_capability(raw_device, status_map)
    if is_rgb_capable:
        brightness_supported = True
    color_supported = bool(resolved_capabilities.get("color_supported")) or is_rgb_capable
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
) -> dict[str, Any]:
    resolved_status_map = status_map or _status_items_to_map(raw_device.get("status"))
    category = str(raw_device.get("category", "")).lower()
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
    capability_snapshot = _capability_snapshot(raw_device, resolved_status_map, capabilities)
    product_name = str(raw_device.get("product_name") or "").strip()
    return {
        "id": str(raw_device.get("id", "")).strip(),
        "short_id": _short_device_id(str(raw_device.get("id", "")).strip()),
        "name": name,
        "category": category or "unknown",
        "type_label": CATEGORY_LABELS.get(category, product_name or "Tuya device"),
        "product_name": product_name,
        "online": online,
        "reachability_label": "Online" if online is True else "Offline" if online is False else "Unknown",
        "power_state": power_state,
        "state_label": power_state.title(),
        "room": room or _extract_room_name(raw_device),
        "status_map": resolved_status_map,
        "raw": raw_device,
        **capability_snapshot,
    }


def _serialize_device_status(status, capabilities: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = asdict(status)
    status_map = dict(status.raw.get("status_map", {}))
    payload["status_map"] = status_map
    payload["power_state"] = status.raw.get("power_state", _derive_power_state(status_map))
    payload["reachability_label"] = "Online" if status.online else "Offline"
    payload["state_label"] = payload["power_state"].title()
    payload.update(_capability_snapshot({"id": status.device_id}, status_map, capabilities))
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

    @app.get("/")
    def index() -> str:
        return render_template("index.html")

    @app.get("/api/status")
    def api_status():
        return jsonify(
            {
                "sync": sync_session.status(),
                "oauth": oauth_session.status(),
                "oauth_callback_url": _oauth_callback_url(),
                "debug_log_count": len(debug_log_session.list()),
                "preview": {"rows": 4, "cols": 4, "target_fps": 4},
                "monitors": list_monitors(),
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
        client = _get_tuya_client(prefer_user_oauth=True)
        try:
            raw_devices = client.list_devices()
        except Exception as exc:
            _record_debug(
                "tuya.list_devices.error",
                {"error": str(exc), "tuya": client.debug_snapshot()},
            )
            raise ValueError(_friendly_tuya_message(str(exc), "No fue posible cargar el catalogo de dispositivos."))
        devices = [
            _normalize_device_record(raw_device)
            for raw_device in raw_devices
        ]
        devices.sort(key=lambda item: (item["name"].lower(), item["id"]))
        _record_debug(
            "tuya.list_devices.success",
            {"device_count": len(devices), "tuya": client.debug_snapshot()},
        )
        return jsonify({"devices": devices, "count": len(devices)})

    @app.post("/api/get-device-status")
    def api_get_device_status():
        payload = request.get_json(silent=True) or {}
        device_id = str(payload.get("device_id", "")).strip()
        if not device_id:
            raise ValueError("device_id is required")
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
        return jsonify(_serialize_device_status(status, capabilities))

    @app.post("/api/set-fixed-color")
    def api_set_fixed_color():
        payload = request.get_json(silent=True) or {}
        device_id = str(payload.get("device_id", "")).strip()
        zone = str(payload.get("zone", "")).strip()
        rgb = _parse_rgb(str(payload.get("rgb", "")).strip())
        _, app_config = load_app_config()
        client = _get_tuya_client(prefer_user_oauth=True)
        if device_id:
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
            return jsonify({"device_id": device_id, "result": result})
        if not zone:
            raise ValueError("device_id or zone is required")
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
        device_id = str(payload.get("device_id", "")).strip()
        zone = str(payload.get("zone", "")).strip()
        state_raw = str(payload.get("state", "")).strip().lower()
        if state_raw not in {"on", "off"}:
            raise ValueError("state must be 'on' or 'off'")
        is_on = state_raw == "on"
        _, app_config = load_app_config()
        client = _get_tuya_client(prefer_user_oauth=True)
        if device_id:
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
            return jsonify({"device_id": device_id, "state": state_raw, "result": result})
        if not zone:
            raise ValueError("device_id or zone is required")
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
        device_id = str(payload.get("device_id", "")).strip()
        if not device_id:
            raise ValueError("device_id is required")
        try:
            level = int(payload.get("level"))
        except (TypeError, ValueError):
            raise ValueError("level must be an integer between 0 and 100")
        level = max(0, min(level, 100))
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
            {"device_id": device_id, "level": level, "tuya": client.debug_snapshot()},
        )
        return jsonify({"device_id": device_id, "level": level, "result": result})

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
        monitor_index_raw = request.args.get("monitor_index", "").strip()
        monitor_index = int(monitor_index_raw) if monitor_index_raw else app_config.capture.monitor_index
        capture_config = replace(app_config.capture, monitor_index=monitor_index)
        capture = ScreenCaptureService(capture_config)
        try:
            frame = capture.capture_frame()
            payload = preview_session.sample(frame, app_config.smoothing)
        except Exception as exc:
            _record_debug(
                "preview.grid.error",
                {"monitor_index": monitor_index, "error": str(exc)},
            )
            raise ValueError("No fue posible capturar la vista previa de pantalla.")

        payload.update(
            {
                "monitor_index": monitor_index,
                "frame_shape": list(frame.shape),
                "monitors": list_monitors(),
            }
        )
        return jsonify(payload)

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
    @app.errorhandler(TuyaApiError)
    @app.errorhandler(ValueError)
    @app.errorhandler(RuntimeError)
    def handle_expected_error(error: Exception):
        return jsonify({"error": str(error)}), 400

    @app.errorhandler(Exception)
    def handle_unexpected_error(error: Exception):  # pragma: no cover
        return jsonify({"error": f"Unexpected server error: {error}"}), 500

    return app
