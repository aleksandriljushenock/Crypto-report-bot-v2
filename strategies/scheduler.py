from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from core.runtime_config import boolean, integer, string
from core import runtime_state
from strategies.catalog import STRATEGIES, get_strategy
from strategies.service import run_strategy_scan, is_strategy_scan_running
from scanner.pipeline import is_trade_scan_running

_LOCK = threading.Lock()
_INDEX = 0
_LAST: dict[str, Any] = {}


def enabled() -> bool:
    return boolean("STRATEGY_LAB_AUTO_ENABLED", True)


def interval_minutes() -> int:
    return integer("STRATEGY_LAB_AUTO_INTERVAL_MINUTES", 30, minimum=5, maximum=1440)


def mode() -> str:
    raw = string("STRATEGY_LAB_AUTO_MODE", "round_robin", strategy=False).strip().lower()
    return raw if raw in {"round_robin", "all"} else "round_robin"


def status() -> dict[str, Any]:
    return {
        "enabled": enabled(),
        "interval_minutes": interval_minutes(),
        "mode": mode(),
        "running": is_strategy_scan_running(),
        "last": dict(_LAST),
        "runtime": runtime_state.get("strategy_auto"),
    }


def _next_spec():
    global _INDEX
    spec = STRATEGIES[_INDEX % len(STRATEGIES)]
    _INDEX = (_INDEX + 1) % len(STRATEGIES)
    return spec


def run_scheduled_cycle() -> dict[str, Any]:
    if not enabled():
        return {"status": "disabled"}
    if is_trade_scan_running():
        return {"status": "skipped-main-scanner"}
    if is_strategy_scan_running():
        return {"status": "skipped-strategy-busy"}
    if not _LOCK.acquire(blocking=False):
        return {"status": "skipped-scheduler-busy"}
    runtime_state.start("strategy_auto", phase="select", processed=0, total=1)
    started = datetime.now(timezone.utc).isoformat()
    try:
        specs = list(STRATEGIES) if mode() == "all" else [_next_spec()]
        outputs = []
        for idx, spec in enumerate(specs, 1):
            if is_trade_scan_running():
                outputs.append({"strategy": spec.key, "status": "skipped-main-scanner"})
                break
            runtime_state.update("strategy_auto", phase="scan", name=spec.key, processed=idx-1, total=len(specs))
            result = run_strategy_scan(spec.key)
            summary = result.get("summary") or {}
            outputs.append({
                "strategy": spec.key,
                "title": spec.title,
                "ready": int(summary.get("ready") or 0),
                "watch": int(summary.get("watch") or 0),
                "analyzed": int(summary.get("analyzed") or 0),
                "busy": bool(summary.get("busy")),
            })
        payload = {"status": "ok", "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(), "runs": outputs}
        _LAST.clear(); _LAST.update(payload)
        return payload
    finally:
        runtime_state.finish("strategy_auto", phase="idle", processed=1, total=1, last=_LAST)
        _LOCK.release()
