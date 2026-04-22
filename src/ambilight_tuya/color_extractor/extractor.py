from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import numpy as np

from ambilight_tuya.models import ColorExtractionConfig, HSVColor, RGBColor, ZoneConfig


@dataclass
class ColorSample:
    zone_name: str
    rgb: RGBColor
    hsv: HSVColor


class ColorExtractor:
    def __init__(self, config: ColorExtractionConfig) -> None:
        self.config = config

    def extract(self, frame: np.ndarray) -> dict[str, ColorSample]:
        samples: dict[str, ColorSample] = {}
        for zone in self.config.zones:
            region = self._crop_zone(frame, zone)
            rgb = self._extract_zone_color(region)
            samples[zone.name] = ColorSample(zone_name=zone.name, rgb=rgb, hsv=rgb.to_hsv())
        return samples

    def _crop_zone(self, frame: np.ndarray, zone: ZoneConfig) -> np.ndarray:
        height, width, _ = frame.shape
        x0 = min(width - 1, int(width * zone.x))
        y0 = min(height - 1, int(height * zone.y))
        x1 = max(x0 + 1, min(width, int(width * (zone.x + zone.width))))
        y1 = max(y0 + 1, min(height, int(height * (zone.y + zone.height))))
        return frame[y0:y1, x0:x1]

    def _extract_zone_color(self, region: np.ndarray) -> RGBColor:
        if self.config.strategy == "dominant":
            pixels = region.reshape(-1, 3)
            quantized = (pixels // 32) * 32
            most_common = Counter(map(tuple, quantized.tolist())).most_common(1)[0][0]
            return RGBColor(*most_common)

        mean_rgb = region.reshape(-1, 3).mean(axis=0)
        return RGBColor(*mean_rgb)
