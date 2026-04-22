from __future__ import annotations

import time

from ambilight_tuya.models import RGBColor, SmoothingConfig


class TemporalColorSmoother:
    def __init__(self, config: SmoothingConfig) -> None:
        self.config = config
        self._last_sent: dict[str, RGBColor] = {}
        self._last_update_ms: dict[str, float] = {}

    def next_color(self, zone_name: str, target: RGBColor, now_ms: float | None = None) -> RGBColor | None:
        current_ms = now_ms if now_ms is not None else time.time() * 1000
        previous = self._last_sent.get(zone_name)
        if previous is None:
            self._last_sent[zone_name] = target
            self._last_update_ms[zone_name] = current_ms
            return target

        if target.distance(previous) < self.config.min_color_delta:
            return None

        if current_ms - self._last_update_ms.get(zone_name, 0.0) < self.config.min_update_interval_ms:
            return None

        blended = previous.blend(target, self.config.alpha)
        self._last_sent[zone_name] = blended
        self._last_update_ms[zone_name] = current_ms
        return blended
