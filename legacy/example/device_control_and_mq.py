"""Legacy sample kept only as historical reference.

Prefer the scripts in `/scripts` and the modules under `src/ambilight_tuya/`.
"""

import logging
import os

from dotenv import load_dotenv

from tuya_connector import TUYA_LOGGER, TuyaCloudPulsarTopic, TuyaOpenAPI, TuyaOpenPulsar


def main() -> None:
    load_dotenv()
    access_id = os.getenv("TUYA_ACCESS_ID", "")
    access_key = os.getenv("TUYA_ACCESS_KEY", "")
    api_endpoint = os.getenv("TUYA_API_ENDPOINT", "")
    mq_endpoint = os.getenv("TUYA_MQ_ENDPOINT", "")

    if not all([access_id, access_key, api_endpoint, mq_endpoint]):
        raise RuntimeError("Missing Tuya credentials in environment variables")

    TUYA_LOGGER.setLevel(logging.DEBUG)
    openapi = TuyaOpenAPI(api_endpoint, access_id, access_key)
    openapi.connect()
    print(openapi.get("/v1.0/statistics-datas-survey", {}))

    open_pulsar = TuyaOpenPulsar(
        access_id,
        access_key,
        mq_endpoint,
        TuyaCloudPulsarTopic.PROD,
    )
    open_pulsar.add_message_listener(lambda msg: print(f"---\nlegacy sample receive: {msg}"))
    open_pulsar.start()
    input("Press Enter to stop legacy MQ sample...")
    open_pulsar.stop()


if __name__ == "__main__":
    main()
