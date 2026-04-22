from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
import yaml

from ambilight_tuya.models import (
    AppConfig,
    CaptureConfig,
    ColorExtractionConfig,
    CommandProfile,
    DeviceMapping,
    SmoothingConfig,
    TuyaCredentials,
    ZoneConfig,
)


class ConfigError(ValueError):
    """Raised when required app configuration is invalid."""


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ConfigError("Config file must contain a top-level mapping")
    return data


def _build_capture(data: dict[str, Any]) -> CaptureConfig:
    return CaptureConfig(
        monitor_index=int(data.get("monitor_index", 1)),
        downsample=max(1, int(data.get("downsample", 4))),
        target_fps=max(1, int(data.get("target_fps", 10))),
    )


def _build_extraction(data: dict[str, Any]) -> ColorExtractionConfig:
    zones = tuple(
        ZoneConfig(
            name=str(zone["name"]),
            x=float(zone["x"]),
            y=float(zone["y"]),
            width=float(zone["width"]),
            height=float(zone["height"]),
        )
        for zone in data.get("zones", [])
    )
    if not zones:
        raise ConfigError("At least one extraction zone must be configured")
    zone_names = [zone.name for zone in zones]
    if len(set(zone_names)) != len(zone_names):
        raise ConfigError("Zone names must be unique")
    strategy = str(data.get("strategy", "average")).lower()
    if strategy not in {"average", "dominant"}:
        raise ConfigError("Extraction strategy must be 'average' or 'dominant'")
    return ColorExtractionConfig(
        strategy=strategy,
        min_change=float(data.get("min_change", 8.0)),
        zones=zones,
    )


def _build_smoothing(data: dict[str, Any]) -> SmoothingConfig:
    config = SmoothingConfig(
        alpha=float(data.get("alpha", 0.35)),
        min_update_interval_ms=int(data.get("min_update_interval_ms", 150)),
        min_color_delta=float(data.get("min_color_delta", 10.0)),
    )
    if not 0.0 < config.alpha <= 1.0:
        raise ConfigError("Smoothing alpha must be between 0 and 1")
    return config


def _build_profiles(data: dict[str, Any]) -> dict[str, CommandProfile]:
    profiles: dict[str, CommandProfile] = {}
    for profile_name, profile_data in data.items():
        profiles[profile_name] = CommandProfile(
            power_code=str(profile_data.get("power_code", "switch_led")),
            color_mode_code=str(profile_data.get("color_mode_code", "work_mode")),
            color_mode_value=str(profile_data.get("color_mode_value", "colour")),
            color_data_code=str(profile_data.get("color_data_code", "colour_data_v2")),
        )
    if "default" not in profiles:
        profiles["default"] = CommandProfile()
    return profiles


def _build_mappings(data: list[dict[str, Any]]) -> tuple[DeviceMapping, ...]:
    mappings: list[DeviceMapping] = []
    for item in data:
        device_ids = item.get("device_ids")
        if device_ids is None and item.get("device_id"):
            device_ids = [item["device_id"]]
        mappings.append(
            DeviceMapping(
                zone=str(item["zone"]),
                device_ids=tuple(str(device_id) for device_id in (device_ids or [])),
                profile=str(item.get("profile", "default")),
            )
        )
    return tuple(mappings)


def load_tuya_credentials() -> TuyaCredentials:
    load_dotenv()
    return TuyaCredentials(
        access_id=_require_env("TUYA_ACCESS_ID"),
        access_key=_require_env("TUYA_ACCESS_KEY"),
        api_endpoint=_require_env("TUYA_API_ENDPOINT"),
        auth_scheme=os.getenv("TUYA_AUTH_SCHEME", "auto").strip().lower() or "auto",
        app_identifier=os.getenv("TUYA_APP_IDENTIFIER", "com.sebastianprietoa.ambilight.localhost").strip() or None,
        mq_endpoint=os.getenv("TUYA_MQ_ENDPOINT") or None,
        default_device_id=os.getenv("TUYA_DEFAULT_DEVICE_ID") or None,
    )


def load_project_config(config_path: str | Path | None = None) -> AppConfig:
    load_dotenv()
    resolved_path = Path(
        config_path
        or os.getenv("AMBILIGHT_CONFIG_PATH", "config/config.yaml")
    )
    data = _load_yaml(resolved_path)

    app_config = AppConfig(
        capture=_build_capture(data.get("capture", {})),
        extraction=_build_extraction(data.get("extraction", {})),
        smoothing=_build_smoothing(data.get("smoothing", {})),
        command_profiles=_build_profiles(data.get("tuya", {}).get("command_profiles", {})),
        mappings=_build_mappings(data.get("mapping", [])),
    )
    configured_zones = {zone.name for zone in app_config.extraction.zones}
    unknown_mappings = {mapping.zone for mapping in app_config.mappings if mapping.zone not in configured_zones}
    if unknown_mappings:
        raise ConfigError(f"Mappings reference unknown zones: {sorted(unknown_mappings)}")
    return app_config


def load_app_config(config_path: str | Path | None = None) -> tuple[TuyaCredentials, AppConfig]:
    credentials = load_tuya_credentials()
    return credentials, load_project_config(config_path)
