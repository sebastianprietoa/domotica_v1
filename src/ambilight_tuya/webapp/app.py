from __future__ import annotations

from dataclasses import asdict, replace
import os
import threading
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

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
    load_dotenv()
    configure_logging()
    app = Flask(__name__, template_folder="templates", static_folder="static")
    sync_session = SyncSession()
    oauth_session = OAuthSession()

    def _oauth_callback_url() -> str:
        return os.getenv(
            "TUYA_OAUTH_CALLBACK_URL",
            "http://127.0.0.1:8787/api/tuya/oauth/callback",
        )

    def _get_tuya_client(prefer_user_oauth: bool = False) -> TuyaClient:
        credentials = load_tuya_credentials()
        client = TuyaClient(credentials)
        token_response = oauth_session.get_token_response()
        if token_response is not None:
            client.restore_token_response(token_response)
            return client
        if prefer_user_oauth and credentials.auth_scheme in {"app", "auto"}:
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
                "monitors": list_monitors(),
            }
        )

    @app.post("/api/list-devices")
    def api_list_devices():
        devices = _get_tuya_client(prefer_user_oauth=True).list_devices()
        return jsonify({"devices": devices})

    @app.post("/api/get-device-status")
    def api_get_device_status():
        payload = request.get_json(silent=True) or {}
        device_id = str(payload.get("device_id", "")).strip()
        if not device_id:
            raise ValueError("device_id is required")
        status = _get_tuya_client(prefer_user_oauth=True).get_device_status(device_id)
        return jsonify(asdict(status))

    @app.post("/api/set-fixed-color")
    def api_set_fixed_color():
        payload = request.get_json(silent=True) or {}
        device_id = str(payload.get("device_id", "")).strip()
        zone = str(payload.get("zone", "")).strip()
        rgb = _parse_rgb(str(payload.get("rgb", "")).strip())
        _, app_config = load_app_config()
        client = _get_tuya_client(prefer_user_oauth=True)
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
            result = client.set_power_state(device_id, is_on, app_config.command_profiles["default"])
            return jsonify({"device_id": device_id, "state": state_raw, "result": result})
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
                    "result": client.set_power_state(resolved_device_id, is_on, routing.profile),
                }
            )
        return jsonify({"zone": zone, "state": state_raw, "results": results})

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
        token_response = client.connect_with_authorization_code(code)
        oauth_session.set_token_response(code, token_response)
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
