"""System/runtime application service.

Collects operational state without knowing Telegram formatting.
"""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
from typing import Any

from core.events import recent
from core.runtime_config import scanner_config
from core.runtime_state import get as runtime_get, snapshot


def process_memory_mb() -> tuple[float | None, float | None]:
    current = peak = None
    try:
        status = Path("/proc/self/status").read_text(encoding="utf-8", errors="ignore")
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                current = float(line.split()[1]) / 1024.0
            elif line.startswith("VmHWM:"):
                peak = float(line.split()[1]) / 1024.0
    except Exception:
        pass
    if peak is None:
        try:
            import resource
            peak = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) / 1024.0
        except Exception:
            pass
    return current, peak


def scanner_state() -> dict[str, Any]:
    state = runtime_get("scanner") or {}
    try:
        from trade_engine import is_trade_scan_running
        state["running"] = bool(state.get("running") or is_trade_scan_running())
    except Exception:
        pass
    return state


def last_scan_summary() -> dict[str, Any]:
    try:
        from scanner_intelligence import get_last_scan_intelligence
        return get_last_scan_intelligence() or {}
    except Exception:
        return {}


def runtime_context(**flags: Any) -> dict[str, Any]:
    cfg = scanner_config()
    rss, peak = process_memory_mb()
    return {
        "scanner": scanner_state(),
        "heavy_task": runtime_get("heavy_task") or {},
        "last_scan": last_scan_summary(),
        "memory": {"rss_mb": rss, "peak_mb": peak},
        "scanner_config": {
            "fast_pool": cfg.fast_pool,
            "deep_limit": cfg.top_symbols,
            "batch_size": cfg.batch_size,
            "workers": cfg.workers,
            "hedge_pool": cfg.hedge_pool,
        },
        "flags": flags,
        "updated_at": datetime.now().astimezone().isoformat(),
    }


def status_snapshot():
    return snapshot()


def recent_events(limit=30):
    return recent(limit)
