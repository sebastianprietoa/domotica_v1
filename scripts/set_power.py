from __future__ import annotations

import argparse

from _common import ROOT  # noqa: F401

from ambilight_tuya.config import load_app_config
from ambilight_tuya.device_mapper import DeviceMapper
from ambilight_tuya.tuya_client import TuyaClient
from ambilight_tuya.utils import configure_logging


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device-id")
    parser.add_argument("--zone")
    parser.add_argument("--state", choices=("on", "off"), required=True)
    args = parser.parse_args()

    if not args.device_id and not args.zone:
        parser.error("Provide --device-id or --zone")

    configure_logging()
    credentials, app_config = load_app_config()
    client = TuyaClient(credentials)
    is_on = args.state == "on"

    if args.device_id:
        profile = app_config.command_profiles["default"]
        client.set_power_state(args.device_id, is_on, profile)
        return

    mapper = DeviceMapper(app_config)
    routing = mapper.resolve(args.zone)
    if routing is None or not routing.device_ids:
        raise ValueError(f"No configured devices for zone {args.zone}")
    for device_id in routing.device_ids:
        client.set_power_state(device_id, is_on, routing.profile)


if __name__ == "__main__":
    main()
