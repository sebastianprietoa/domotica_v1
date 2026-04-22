from __future__ import annotations

from dataclasses import asdict, replace
import io
import threading
from typing import Any

from flask import Flask, jsonify, render_template, request
from PIL import Image

from ambilight_tuya.color_extractor import ColorExtractor
from ambilight_tuya.config import ConfigError, load_app_config, load_project_config, load_tuya_credentials
from ambilight_tuya.device_mapper import DeviceMapper
from ambilight_tuya.models import AppConfig, RGBColor
from ambilight_tuya.screen_capture import ScreenCaptureService, list_monitors
from ambilight_tuya.sync_engine import AmbilightSyncEngine
from ambilight_tuya.tuya_client import TuyaClient, TuyaApiError
from ambilight_tuya.utils import configure_logging


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

    def start(self, app_config: AppConfig, credentials, dry_run: bool, duration: float | None) -> dict[str, Any]:
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
                    client = None if dry_run else TuyaClient(credentials)
                    engine = AmbilightSyncEngine(app_config, client)
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


def create_app() -> Flask:
    configure_logging()
    app = Flask(__name__, template_folder="templates", static_folder="static")
    sync_session = SyncSession()

    @app.get("/")
    def index() -> str:
        return render_template("index.html")

    @app.get("/api/status")
    def api_status():
        return jsonify(
            {
                "sync": sync_session.status(),
                "monitors": list_monitors(),
            }
        )

    @app.post("/api/list-devices")
    def api_list_devices():
        credentials, _ = load_app_config()
        devices = TuyaClient(credentials).list_devices()
        return jsonify({"devices": devices})

    @app.post("/api/get-device-status")
    def api_get_device_status():
        payload = request.get_json(silent=True) or {}
        device_id = str(payload.get("device_id", "")).strip()
        if not device_id:
            raise ValueError("device_id is required")
        credentials, _ = load_app_config()
        status = TuyaClient(credentials).get_device_status(device_id)
        return jsonify(asdict(status))

    @app.post("/api/set-fixed-color")
    def api_set_fixed_color():
        payload = request.get_json(silent=True) or {}
        device_id = str(payload.get("device_id", "")).strip()
        zone = str(payload.get("zone", "")).strip()
        rgb = _parse_rgb(str(payload.get("rgb", "")).strip())
        credentials, app_config = load_app_config()
        client = TuyaClient(credentials)
        if device_id:
            result = client.set_fixed_color(device_id, rgb, app_config.command_profiles["default"])
            return jsonify({"device_id": device_id, "result": result})
        if not zone:
            raise ValueError("device_id or zone is required")
        routing = DeviceMapper(app_config).resolve(zone)
        if routing is None or not routing.device_ids:
            raise ValueError(f"No devices configured for zone {zone}")
        results = []
        for resolved_device_id in routing.device_ids:
            results.append(
                {
                    "device_id": resolved_device_id,
                    "result": client.set_fixed_color(resolved_device_id, rgb, routing.profile),
                }
            )
        return jsonify({"zone": zone, "results": results})

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
        image = Image.fromarray(frame)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return jsonify(
            {
                "monitors": list_monitors(),
                "samples": _serialize_samples(samples),
                "preview_png_base64": buffer.getvalue().hex(),
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
        credentials = None if dry_run else load_tuya_credentials()
        if monitor_index is not None:
            app_config = replace(
                app_config,
                capture=replace(app_config.capture, monitor_index=int(monitor_index)),
            )
        return jsonify(sync_session.start(app_config, credentials, dry_run=dry_run, duration=duration))

    @app.post("/api/sync/stop")
    def api_sync_stop():
        return jsonify(sync_session.stop())

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
