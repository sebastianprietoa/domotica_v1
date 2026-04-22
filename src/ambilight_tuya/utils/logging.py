from __future__ import annotations

import logging
import os


def configure_logging(level: str | None = None) -> None:
    logging.basicConfig(
        level=getattr(logging, (level or os.getenv("AMBILIGHT_LOG_LEVEL", "INFO")).upper(), logging.INFO),
        format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    )
