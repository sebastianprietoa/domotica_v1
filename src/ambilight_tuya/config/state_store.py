from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


DEFAULT_STATE = {
    "ambilight_mapping": {},
}


class DashboardStateStore:
    def __init__(self, path: str | Path | None = None) -> None:
        load_dotenv()
        resolved = path or os.getenv("AMBILIGHT_STATE_PATH", "config/dashboard_state.json")
        self.path = Path(resolved)
        self._lock = threading.Lock()

    def _read_state(self) -> dict[str, Any]:
        if not self.path.exists():
            return dict(DEFAULT_STATE)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return dict(DEFAULT_STATE)
        if not isinstance(raw, dict):
            return dict(DEFAULT_STATE)
        return {**DEFAULT_STATE, **raw}

    def _write_state(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(state, indent=2, ensure_ascii=True, sort_keys=True),
            encoding="utf-8",
        )

    def get_ambilight_mapping(self) -> dict[str, dict[str, str]]:
        with self._lock:
            state = self._read_state()
            raw_mapping = state.get("ambilight_mapping", {})
            if not isinstance(raw_mapping, dict):
                return {}
            mapping: dict[str, dict[str, str]] = {}
            for cell_key, target in raw_mapping.items():
                if not isinstance(target, dict):
                    continue
                provider = str(target.get("provider", "")).strip().lower()
                device_id = str(target.get("device_id", "")).strip()
                if provider and device_id:
                    mapping[str(cell_key)] = {
                        "provider": provider,
                        "device_id": device_id,
                    }
            return mapping

    def save_ambilight_mapping(self, mapping: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        cleaned: dict[str, dict[str, str]] = {}
        for cell_key, target in mapping.items():
            if not isinstance(target, dict):
                continue
            provider = str(target.get("provider", "")).strip().lower()
            device_id = str(target.get("device_id", "")).strip()
            if provider and device_id:
                cleaned[str(cell_key)] = {
                    "provider": provider,
                    "device_id": device_id,
                }
        with self._lock:
            state = self._read_state()
            state["ambilight_mapping"] = cleaned
            self._write_state(state)
        return cleaned
