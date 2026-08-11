from __future__ import annotations
from typing import Any


def _client():
    from cloud_client import get_supabase_client
    return get_supabase_client()


class StrategyRepository:
    def save_run(self, strategy: str, summary: dict[str, Any], candidates: list[dict[str, Any]]) -> None:
        _client().table("strategy_scan_runs").insert({
            "strategy": strategy,
            "summary": summary,
            "candidates": candidates,
        }).execute()

    def latest_run(self, strategy: str) -> dict[str, Any] | None:
        rows = (_client().table("strategy_scan_runs").select("*").eq("strategy", strategy)
                .order("created_at", desc=True).limit(1).execute().data or [])
        return rows[0] if rows else None

    def upsert_setup(self, row: dict[str, Any]) -> None:
        # Never reset a resolved setup when the same D1 swing is discovered again.
        existing = (_client().table("strategy_setups").select("id,state").eq("fingerprint", row["fingerprint"])
                    .limit(1).execute().data or [])
        if existing:
            return
        _client().table("strategy_setups").insert(row).execute()

    def active_setups(self, strategy: str, limit: int = 100) -> list[dict[str, Any]]:
        rows = (_client().table("strategy_setups").select("*").eq("strategy", strategy)
                .in_("state", ["waiting_entry", "open"]).order("created_at", desc=False)
                .limit(limit).execute().data or [])
        return rows

    def recent_setups(self, strategy: str, limit: int = 1000) -> list[dict[str, Any]]:
        return (_client().table("strategy_setups").select("*").eq("strategy", strategy)
                .order("created_at", desc=True).limit(limit).execute().data or [])

    def update_setup(self, setup_id: Any, values: dict[str, Any]) -> None:
        _client().table("strategy_setups").update(values).eq("id", setup_id).execute()


repository = StrategyRepository()
