from __future__ import annotations

import argparse
import json
from pathlib import Path

from _common import ROOT  # noqa: F401

import numpy as np
from PIL import Image

from ambilight_tuya.color_extractor import ColorExtractor
from ambilight_tuya.config import load_project_config
from ambilight_tuya.screen_capture import ScreenCaptureService, list_monitors
from ambilight_tuya.utils import configure_logging


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--save-preview")
    args = parser.parse_args()

    configure_logging()
    app_config = load_project_config()
    capture = ScreenCaptureService(app_config.capture)
    extractor = ColorExtractor(app_config.extraction)
    frame = capture.capture_frame()
    samples = extractor.extract(frame)

    print(json.dumps(
        {
            "monitors": list_monitors(),
            "samples": {
                name: {"rgb": sample.rgb.as_tuple(), "hsv": sample.hsv.as_dict()}
                for name, sample in samples.items()
            },
        },
        indent=2,
        ensure_ascii=False,
    ))

    if args.save_preview:
        output_path = Path(args.save_preview)
        Image.fromarray(np.asarray(frame)).save(output_path)


if __name__ == "__main__":
    main()
