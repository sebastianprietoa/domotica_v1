from __future__ import annotations

from ambilight_tuya.models import RGBColor, SmoothingConfig
from ambilight_tuya.smoothing import TemporalColorSmoother


def test_first_color_is_emitted_immediately() -> None:
    smoother = TemporalColorSmoother(SmoothingConfig())
    assert smoother.next_color("left", RGBColor(10, 20, 30), now_ms=0) == RGBColor(10, 20, 30)


def test_small_color_changes_are_ignored() -> None:
    smoother = TemporalColorSmoother(SmoothingConfig(min_color_delta=20.0, min_update_interval_ms=0))
    smoother.next_color("left", RGBColor(10, 20, 30), now_ms=0)
    assert smoother.next_color("left", RGBColor(15, 25, 35), now_ms=1000) is None


def test_large_changes_are_blended() -> None:
    smoother = TemporalColorSmoother(SmoothingConfig(alpha=0.5, min_color_delta=1.0, min_update_interval_ms=0))
    smoother.next_color("left", RGBColor(0, 0, 0), now_ms=0)
    assert smoother.next_color("left", RGBColor(100, 50, 0), now_ms=1000) == RGBColor(50, 25, 0)
