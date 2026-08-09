"""Operational diagnostics assembled from one runtime/event source."""
from __future__ import annotations
from typing import Any

from core.events import recent
from core.runtime_state import snapshot
from trade_market_client import get_provider_health_snapshot


def snapshot_report(event_limit: int = 12) -> dict[str, Any]:
    providers = get_provider_health_snapshot()
    problem_providers = [p for p in providers if p.get("status") in {"cooldown", "degraded"}]
    return {
        "runtime": snapshot(),
        "providers": providers,
        "provider_problems": problem_providers,
        "events": recent(event_limit),
    }
