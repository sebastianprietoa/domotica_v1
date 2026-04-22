from __future__ import annotations

import argparse
from dataclasses import replace

from _common import ROOT  # noqa: F401

from ambilight_tuya.config import load_app_config, load_project_config
from ambilight_tuya.sync_engine import AmbilightSyncEngine
from ambilight_tuya.tuya_client import TuyaClient
from ambilight_tuya.utils import configure_logging


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--monitor-index", type=int)
    args = parser.parse_args()

    configure_logging()
    if args.dry_run:
        credentials = None
        app_config = load_project_config()
    else:
        credentials, app_config = load_app_config()
    if args.monitor_index is not None:
        app_config = replace(
            app_config,
            capture=replace(app_config.capture, monitor_index=args.monitor_index),
        )
    tuya_client = TuyaClient(credentials) if credentials is not None else None
    engine = AmbilightSyncEngine(app_config, tuya_client)
    engine.run(duration_seconds=args.duration, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
