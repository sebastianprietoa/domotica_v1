from __future__ import annotations

from dataclasses import dataclass

from ambilight_tuya.models import AppConfig, CommandProfile, DeviceMapping


@dataclass(frozen=True)
class ZoneRouting:
    zone: str
    device_ids: tuple[str, ...]
    profile: CommandProfile


class DeviceMapper:
    def __init__(self, app_config: AppConfig) -> None:
        self._profiles = app_config.command_profiles
        self._mappings = {mapping.zone: mapping for mapping in app_config.mappings}

    def resolve(self, zone_name: str) -> ZoneRouting | None:
        mapping = self._mappings.get(zone_name)
        if mapping is None:
            return None
        profile = self._profiles.get(mapping.profile) or self._profiles["default"]
        return ZoneRouting(zone=zone_name, device_ids=mapping.device_ids, profile=profile)
