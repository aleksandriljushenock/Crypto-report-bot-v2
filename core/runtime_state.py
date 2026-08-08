"""Thread-safe runtime activity registry.

This module is the single source of truth for *what the bot is doing now*.
It intentionally stores only ephemeral process state. Persistent scanner history
continues to live in Supabase via scanner_intelligence.
"""
from __future__ import annotations

import threading
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

_LOCK = threading.RLock()
_STATE: dict[str, dict[str, Any]] = {
    "scanner": {
        "running": False,
        "owner": None,
        "phase": "idle",
        "processed": 0,
        "total": 0,
        "startedAt": None,
        "updatedAt": None,
    },
    "heavy_task": {
        "running": False,
        "name": None,
        "startedAt": None,
        "updatedAt": None,
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def update(component: str, **values: Any) -> dict[str, Any]:
    """Merge values into a runtime component and return a copy."""
    with _LOCK:
        target = _STATE.setdefault(component, {})
        target.update(values)
        target["updatedAt"] = _now()
        return deepcopy(target)


def get(component: str) -> dict[str, Any]:
    with _LOCK:
        return deepcopy(_STATE.get(component, {}))


def snapshot() -> dict[str, dict[str, Any]]:
    with _LOCK:
        return deepcopy(_STATE)


def start(component: str, *, name: str | None = None, **values: Any) -> dict[str, Any]:
    started = _now()
    payload = {"running": True, "startedAt": started, **values}
    if name is not None:
        payload["name"] = name
    return update(component, **payload)


def finish(component: str, **values: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {"running": False}
    if component == "scanner":
        defaults.update(owner=None, phase="idle", processed=0, total=0)
    elif component == "heavy_task":
        defaults.update(name=None)
    defaults.update(values)
    return update(component, **defaults)
