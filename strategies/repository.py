from __future__ import annotations
from typing import Any
from datetime import datetime, timedelta, timezone


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

    def upsert_setup(self, row: dict[str, Any]) -> bool:
        # Never reset a resolved setup when the same event is discovered again.
        existing = (_client().table("strategy_setups").select("id,state").eq("fingerprint", row["fingerprint"])
                    .limit(1).execute().data or [])
        if existing:
            return False
        _client().table("strategy_setups").insert(row).execute()
        return True

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

    def pending_notifications(self, max_age_hours: int = 24, limit: int = 30) -> list[dict[str, Any]]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
        table = _client().table("strategy_setups")
        out: list[dict[str, Any]] = []

        ready = (table.select("*").eq("state", "waiting_entry")
                 .is_("ready_notified_at", "null").gte("created_at", cutoff)
                 .order("created_at", desc=False).limit(limit).execute().data or [])
        out.extend({"event_type": "READY", "setup": row} for row in ready)

        remaining = max(0, limit - len(out))
        if remaining:
            opened = (_client().table("strategy_setups").select("*").eq("state", "open")
                      .is_("open_notified_at", "null").gte("entered_at", cutoff)
                      .order("entered_at", desc=False).limit(remaining).execute().data or [])
            out.extend({"event_type": "OPEN", "setup": row} for row in opened)

        remaining = max(0, limit - len(out))
        if remaining:
            closed = (_client().table("strategy_setups").select("*").in_("state", ["won", "lost"])
                      .is_("close_notified_at", "null").gte("resolved_at", cutoff)
                      .order("resolved_at", desc=False).limit(remaining).execute().data or [])
            out.extend({"event_type": "CLOSED", "setup": row} for row in closed)
        return out[:limit]

    def mark_notification_sent(self, setup_id: Any, event_type: str) -> None:
        event_type = str(event_type or "").upper()
        column = {"READY": "ready_notified_at", "OPEN": "open_notified_at", "CLOSED": "close_notified_at"}.get(event_type)
        if not column or setup_id is None:
            return
        _client().table("strategy_setups").update({column: datetime.now(timezone.utc).isoformat()}).eq("id", setup_id).execute()


repository = StrategyRepository()
