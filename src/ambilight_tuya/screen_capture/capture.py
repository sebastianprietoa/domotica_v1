from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import mss
import numpy as np

from ambilight_tuya.models import CaptureConfig


def list_monitors() -> list[dict[str, int]]:
    with mss.mss() as sct:
        return [dict(monitor) for monitor in sct.monitors[1:]]


@dataclass
class ScreenCaptureService:
    config: CaptureConfig

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
