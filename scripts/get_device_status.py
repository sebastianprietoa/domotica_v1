from __future__ import annotations

import argparse
import json

from _common import ROOT  # noqa: F401

from ambilight_tuya.config import load_app_config
from ambilight_tuya.tuya_client import TuyaClient
from ambilight_tuya.utils import configure_logging


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device-id", required=True)
    args = parser.parse_args()

    configure_logging()
    credentials, _ = load_app_config()
    client = TuyaClient(credentials)
    status = client.get_device_status(args.device_id)
    print(json.dumps(status.raw, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
