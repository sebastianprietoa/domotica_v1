from __future__ import annotations

import logging
import threading
import time

from ambilight_tuya.color_extractor import ColorExtractor
from ambilight_tuya.device_mapper import DeviceMapper
from ambilight_tuya.models import AppConfig, RGBColor
from ambilight_tuya.screen_capture import ScreenCaptureService
from ambilight_tuya.smoothing import TemporalColorSmoother
from ambilight_tuya.tuya_client import TuyaClient


logger = logging.getLogger(__name__)


class AmbilightSyncEngine:
    def __init__(self, app_config: AppConfig, tuya_client: TuyaClient | None = None) -> None:
        self.app_config = app_config
        self.tuya_client = tuya_client
        self.capture = ScreenCaptureService(app_config.capture)
        self.extractor = ColorExtractor(app_config.extraction)
        self.smoother = TemporalColorSmoother(app_config.smoothing)
        self.mapper = DeviceMapper(app_config)

    def process_once(self, dry_run: bool = False) -> dict[str, RGBColor]:
        frame = self.capture.capture_frame()
        samples = self.extractor.extract(frame)
        output: dict[str, RGBColor] = {}
        for zone_name, sample in samples.items():
            routing = self.mapper.resolve(zone_name)
            if routing is None or not routing.device_ids:
                continue
            smoothed = self.smoother.next_color(zone_name, sample.rgb)
            if smoothed is None:
                continue
            output[zone_name] = smoothed
            if dry_run:
                logger.info("Dry-run zone=%s color=%s devices=%s", zone_name, smoothed.as_tuple(), routing.device_ids)
                continue
            if self.tuya_client is None:
                raise RuntimeError("Tuya client is required when dry_run is False")
            for device_id in routing.device_ids:
                self.tuya_client.set_fixed_color(device_id, smoothed, routing.profile)
        return output

    def run(
        self,
        duration_seconds: float | None = None,
        dry_run: bool = False,
        stop_event: threading.Event | None = None,
    ) -> None:
        start = time.perf_counter()
        interval = 1.0 / self.app_config.capture.target_fps
        while stop_event is None or not stop_event.is_set():
            loop_start = time.perf_counter()
            self.process_once(dry_run=dry_run)
            if duration_seconds is not None and (loop_start - start) >= duration_seconds:
                return
            elapsed = time.perf_counter() - loop_start
            if stop_event is not None:
                stop_event.wait(max(0.0, interval - elapsed))
            else:
                time.sleep(max(0.0, interval - elapsed))
