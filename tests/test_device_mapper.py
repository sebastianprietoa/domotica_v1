from __future__ import annotations

from ambilight_tuya.device_mapper import DeviceMapper
from ambilight_tuya.models import (
    AppConfig,
    CaptureConfig,
    ColorExtractionConfig,
    CommandProfile,
    DeviceMapping,
    SmoothingConfig,
    ZoneConfig,
)


def test_device_mapper_resolves_zone_to_multiple_devices() -> None:
    app_config = AppConfig(
        capture=CaptureConfig(),
        extraction=ColorExtractionConfig(zones=(ZoneConfig(name="left", x=0, y=0, width=1, height=1),)),
        smoothing=SmoothingConfig(),
        command_profiles={"default": CommandProfile()},
        mappings=(DeviceMapping(zone="left", device_ids=("a", "b"), profile="default"),),
    )

    routing = DeviceMapper(app_config).resolve("left")

    assert routing is not None
    assert routing.device_ids == ("a", "b")
