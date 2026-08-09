"""Small process event stream used by Status/Diagnostics and audit trails."""
from __future__ import annotations
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

_LOCK = threading.RLock()
_EVENTS = deque(maxlen=1000)

def emit(event: str, **payload: Any) -> dict[str, Any]:
    row = {"event": event, "at": datetime.now(timezone.utc).isoformat(), **payload}
    with _LOCK:
        _EVENTS.append(row)
    return dict(row)

def recent(limit: int = 50, event: str | None = None) -> list[dict[str, Any]]:
    with _LOCK:
        rows = list(_EVENTS)
    if event:
        rows = [x for x in rows if x.get("event") == event]
    return [dict(x) for x in rows[-max(1, min(limit, 500)):]]
