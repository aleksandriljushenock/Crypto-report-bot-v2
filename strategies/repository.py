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

    def active_setups(self, strategy: str, limit: int = 5000) -> list[dict[str, Any]]:
        out = []; offset = 0; cap = max(1, int(limit or 5000))
        while offset < cap:
            end = min(offset + 999, cap - 1)
            rows = (_client().table("strategy_setups").select("*").eq("strategy", strategy)
                    .in_("state", ["waiting_entry", "open"]).order("created_at", desc=True)
                    .range(offset, end).execute().data or [])
            out.extend(rows)
            if len(rows) < (end - offset + 1): break
            offset = end + 1
        return out

    def recent_setups(self, strategy: str, limit: int = 1000) -> list[dict[str, Any]]:
        return (_client().table("strategy_setups").select("*").eq("strategy", strategy)
                .order("created_at", desc=True).limit(limit).execute().data or [])

    def setups_for_stats(self, strategy: str, page_size: int = 1000, max_rows: int | None = None) -> list[dict[str, Any]]:
        """Load the durable strategy history without the old 2k-statistics truncation.

        Supabase/PostgREST commonly caps a response near 1k rows, so statistics are
        paged explicitly. The safety cap prevents an accidental unbounded read.
        """
        out: list[dict[str, Any]] = []
        start = 0
        page_size = max(100, min(int(page_size or 1000), 1000))
        cap = max(page_size, int(max_rows)) if max_rows is not None and int(max_rows) > 0 else None
        while cap is None or start < cap:
            end = start + page_size - 1 if cap is None else min(start + page_size - 1, cap - 1)
            rows = (_client().table("strategy_setups").select("*").eq("strategy", strategy)
                    .order("created_at", desc=False).range(start, end).execute().data or [])
            out.extend(rows)
            if len(rows) < page_size:
                break
            start += page_size
        return out

    def upsert_statistics(self, strategy: str, metrics: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        payload = {"strategy": strategy, **metrics, "updated_at": now}
        _client().table("strategy_statistics").upsert(payload, on_conflict="strategy").execute()

    def save_daily_statistics(self, strategy: str, metrics: dict[str, Any]) -> None:
        now_dt = datetime.now(timezone.utc)
        payload = {
            "strategy": strategy,
            "stat_date": now_dt.date().isoformat(),
            "metrics": metrics,
            "updated_at": now_dt.isoformat(),
        }
        _client().table("strategy_stats_daily").upsert(payload, on_conflict="strategy,stat_date").execute()

    def persisted_statistics(self, strategy: str) -> dict[str, Any] | None:
        rows = (_client().table("strategy_statistics").select("*").eq("strategy", strategy)
                .limit(1).execute().data or [])
        return rows[0] if rows else None

    def update_setup(self, setup_id: Any, values: dict[str, Any]) -> None:
        _client().table("strategy_setups").update(values).eq("id", setup_id).execute()

    def pending_notifications(self, max_age_hours: int = 24, limit: int = 30, strategies: list[str] | None = None) -> list[dict[str, Any]]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
        table = _client().table("strategy_setups")
        out: list[dict[str, Any]] = []

        ready_q = table.select("*").eq("state", "waiting_entry")
        if strategies:
            ready_q = ready_q.in_("strategy", strategies)
        ready = (ready_q
                 .is_("ready_notified_at", "null").gte("created_at", cutoff)
                 .order("created_at", desc=False).limit(limit).execute().data or [])
        out.extend({"event_type": "READY", "setup": row} for row in ready)

        remaining = max(0, limit - len(out))
        if remaining:
            opened_q = _client().table("strategy_setups").select("*").eq("state", "open")
            if strategies:
                opened_q = opened_q.in_("strategy", strategies)
            opened = (opened_q
                      .is_("open_notified_at", "null").gte("entered_at", cutoff)
                      .order("entered_at", desc=False).limit(remaining).execute().data or [])
            out.extend({"event_type": "OPEN", "setup": row} for row in opened)

        remaining = max(0, limit - len(out))
        if remaining:
            closed_q = _client().table("strategy_setups").select("*").in_("state", ["won", "lost", "breakeven"])
            if strategies:
                closed_q = closed_q.in_("strategy", strategies)
            closed = (closed_q
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
