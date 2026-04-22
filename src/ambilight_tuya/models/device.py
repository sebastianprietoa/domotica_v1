from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DeviceStatus:
    device_id: str
    online: bool
    raw: dict[str, Any] = field(default_factory=dict)
