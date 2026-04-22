from __future__ import annotations

import json

from _common import ROOT  # noqa: F401

from ambilight_tuya.config import load_app_config
from ambilight_tuya.tuya_client import TuyaClient
from ambilight_tuya.utils import configure_logging


def main() -> None:
    configure_logging()
    credentials, _ = load_app_config()
    client = TuyaClient(credentials)
    devices = client.list_devices()
    print(json.dumps(devices, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
