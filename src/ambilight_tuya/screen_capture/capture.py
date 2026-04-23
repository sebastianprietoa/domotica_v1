from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import mss
import numpy as np

from ambilight_tuya.models import CaptureConfig


def _normalize_monitor(monitor: dict[str, Any], index: int) -> dict[str, int | bool]:
    return {
        "index": index,
        "left": int(monitor["left"]),
        "top": int(monitor["top"]),
        "width": int(monitor["width"]),
        "height": int(monitor["height"]),
        # mss monitor index 1 is the primary source monitor used by default.
        "is_primary": index == 1,
    }


def list_monitors() -> list[dict[str, int]]:
    with mss.mss() as sct:
        return [
            _normalize_monitor(monitor, index)
            for index, monitor in enumerate(sct.monitors[1:], start=1)
        ]


@dataclass
class ScreenCaptureService:
    config: CaptureConfig

    def monitor_metadata(self) -> dict[str, int | bool]:
        with mss.mss() as sct:
            monitors = sct.monitors
            if self.config.monitor_index >= len(monitors):
                raise IndexError(
                    f"Monitor index {self.config.monitor_index} is out of range; found {len(monitors) - 1} monitors"
                )
            return _normalize_monitor(monitors[self.config.monitor_index], self.config.monitor_index)

    def capture_frame(self) -> np.ndarray:
        with mss.mss() as sct:
            monitors = sct.monitors
            if self.config.monitor_index >= len(monitors):
                raise IndexError(
                    f"Monitor index {self.config.monitor_index} is out of range; found {len(monitors) - 1} monitors"
                )
            screenshot = sct.grab(monitors[self.config.monitor_index])
            frame = np.asarray(screenshot, dtype=np.uint8)[..., :3]
            rgb_frame = frame[..., ::-1]
            factor = max(1, self.config.downsample)
            return rgb_frame[::factor, ::factor]

    def capture_frame_with_metadata(self) -> tuple[np.ndarray, dict[str, int | bool]]:
        return self.capture_frame(), self.monitor_metadata()
