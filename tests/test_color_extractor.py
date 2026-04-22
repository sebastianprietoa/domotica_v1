from __future__ import annotations

import numpy as np

from ambilight_tuya.color_extractor import ColorExtractor
from ambilight_tuya.models import ColorExtractionConfig, ZoneConfig


def test_average_color_extractor_returns_zone_colors() -> None:
    frame = np.zeros((2, 4, 3), dtype=np.uint8)
    frame[:, :2] = [255, 0, 0]
    frame[:, 2:] = [0, 0, 255]
    config = ColorExtractionConfig(
        strategy="average",
        zones=(
            ZoneConfig(name="left", x=0.0, y=0.0, width=0.5, height=1.0),
            ZoneConfig(name="right", x=0.5, y=0.0, width=0.5, height=1.0),
        ),
    )

    result = ColorExtractor(config).extract(frame)

    assert result["left"].rgb.as_tuple() == (255, 0, 0)
    assert result["right"].rgb.as_tuple() == (0, 0, 255)


def test_dominant_color_extractor_quantizes_output() -> None:
    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    frame[:] = [250, 10, 10]
    config = ColorExtractionConfig(
        strategy="dominant",
        zones=(ZoneConfig(name="all", x=0.0, y=0.0, width=1.0, height=1.0),),
    )

    result = ColorExtractor(config).extract(frame)

    assert result["all"].rgb.as_tuple() == (224, 0, 0)
