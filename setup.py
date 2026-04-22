#!/usr/bin/env python
# -*- coding: UTF-8 -*-

from pathlib import Path

from setuptools import find_packages, setup

from tuya_connector import __version__


ROOT = Path(__file__).parent


def requirements() -> list[str]:
    return [line.strip() for line in (ROOT / "requirements.txt").read_text().splitlines() if line.strip()]


setup(
    name="ambilight-tuya-pc",
    version=__version__,
    url="https://github.com/sebastianprietoa/domotica_v1",
    author="sebastianprietoa",
    author_email="opensource@example.com",
    keywords="tuya ambilight rgb windows automation",
    description="Clean Ambilight architecture for Tuya RGB lights on Windows.",
    long_description=(ROOT / "README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    license="MIT",
    project_urls={
        "Source": "https://github.com/sebastianprietoa/domotica_v1",
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "License :: OSI Approved :: MIT License",
        "Operating System :: Microsoft :: Windows",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.11",
        "Topic :: Home Automation",
    ],
    install_requires=requirements(),
    packages=find_packages(where="src") + ["tuya_connector"],
    package_dir={"": "src", "tuya_connector": "tuya_connector"},
    python_requires=">=3.11",
)
