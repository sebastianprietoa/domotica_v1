from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TuyaCredentials:
    access_id: str
    access_key: str
    api_endpoint: str
    auth_scheme: str = "auto"
    app_identifier: str | None = None
    mq_endpoint: str | None = None
    default_device_id: str | None = None


@dataclass(frozen=True)
class CaptureConfig:
    monitor_index: int = 1
    downsample: int = 4
    target_fps: int = 10


@dataclass(frozen=True)
class ZoneConfig:
    name: str
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class ColorExtractionConfig:
    strategy: str = "average"
    min_change: float = 8.0
    zones: tuple[ZoneConfig, ...] = ()


@dataclass(frozen=True)
class SmoothingConfig:
    alpha: float = 0.35
    min_update_interval_ms: int = 150
    min_color_delta: float = 10.0


@dataclass(frozen=True)
class CommandProfile:
    power_code: str = "switch_led"
    color_mode_code: str = "work_mode"
    color_mode_value: str = "colour"
    color_data_code: str = "colour_data_v2"


@dataclass(frozen=True)
class DeviceMapping:
    zone: str
    device_ids: tuple[str, ...] = ()
    profile: str = "default"


@dataclass(frozen=True)
class AppConfig:
    capture: CaptureConfig
    extraction: ColorExtractionConfig
    smoothing: SmoothingConfig
    command_profiles: dict[str, CommandProfile] = field(default_factory=dict)
    mappings: tuple[DeviceMapping, ...] = ()
