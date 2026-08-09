"""Paper Trading persistence boundary.

All Supabase table knowledge for the execution simulator lives here. The domain
and optimizer layers consume repository methods instead of constructing queries.
"""
from __future__ import annotations
from typing import Any

from trading.domain import is_valid_closed_position


def _client():
    from cloud_client import get_supabase_client
    return get_supabase_client()


class PaperRepository:
    def account(self, account_id: str) -> dict[str, Any] | None:
        data = _client().table("paper_accounts").select("*").eq("id", account_id).limit(1).execute().data or []
        return data[0] if data else None

    def insert_account(self, row: dict[str, Any]) -> dict[str, Any]:
        data = _client().table("paper_accounts").insert(row).execute().data or []
        return data[0] if data else dict(row)

    def update_account(self, account_id: str, values: dict[str, Any]) -> None:
        _client().table("paper_accounts").update(values).eq("id", account_id).execute()

    def upsert_account(self, account_id: str, values: dict[str, Any]) -> None:
        _client().table("paper_accounts").upsert({"id": account_id, **values}, on_conflict="id").execute()

    def positions_by_status(self, status: str, order_by: str = "created_at") -> list[dict[str, Any]]:
        return (_client().table("paper_positions").select("*").eq("status", status)
                .order(order_by).execute().data or [])

    def position_by_fingerprint(self, fingerprint: str) -> dict[str, Any] | None:
        data = (_client().table("paper_positions").select("id,status").eq("fingerprint", fingerprint)
                .limit(1).execute().data or [])
        return data[0] if data else None

    def insert_position(self, row: dict[str, Any]) -> dict[str, Any]:
        data = _client().table("paper_positions").insert(row).execute().data or []
        return data[0] if data else dict(row)

    def update_position(self, position_id: Any, values: dict[str, Any], expected_status: str | None = None) -> dict[str, Any] | None:
        query = _client().table("paper_positions").update(values).eq("id", position_id)
        if expected_status:
            query = query.eq("status", expected_status)
        data = query.execute().data or []
        return data[0] if data else None

    def insert_trade(self, row: dict[str, Any]) -> None:
        _client().table("paper_trades").insert(row).execute()

    def upsert_trade(self, row: dict[str, Any]) -> None:
        """Idempotent trade persistence keyed by position_id/fingerprint.

        The v24 migration adds a unique index on position_id. Upsert prevents a
        duplicate close event from creating duplicate realized PnL history.
        """
        _client().table("paper_trades").upsert(row, on_conflict="position_id").execute()

    def recent_trades(self, limit: int = 20, *, valid_only: bool = True) -> list[dict[str, Any]]:
        max_rows = max(1, min(limit, 1000))
        trades = (_client().table("paper_trades").select("*").order("closed_at", desc=True)
                  .limit(max_rows).execute().data or [])
        if not valid_only or not trades:
            return trades
        # A trade is eligible for PnL/learning only if its originating position
        # reached a real OPEN fill and then CLOSED. This filters legacy phantom fills.
        positions = self.valid_closed_positions(max(max_rows * 2, 1000), ascending=False)
        valid_fp = {str(row.get("fingerprint") or "") for row in positions if row.get("fingerprint")}
        valid_ids = {str(row.get("id") or "") for row in positions if row.get("id")}
        return [row for row in trades if str(row.get("fingerprint") or "") in valid_fp or str(row.get("position_id") or "") in valid_ids]

    def delete_account_history(self, account_id: str) -> None:
        _client().table("paper_trades").delete().eq("account_id", account_id).execute()
        _client().table("paper_positions").delete().eq("account_id", account_id).execute()

    def valid_closed_positions(self, limit: int = 1000, *, ascending: bool = False) -> list[dict[str, Any]]:
        fields = (
            "id,fingerprint,symbol,status,entry_price,margin_usd,net_pnl,quality_score,probability,"
            "expected_value_pct,signal_payload,opened_at,closed_at,close_reason,strategy_version,fill_price_source"
        )
        rows = (_client().table("paper_positions").select(fields).eq("status", "closed")
                .order("closed_at", desc=not ascending).limit(max(1, min(limit, 5000))).execute().data or [])
        return [row for row in rows if is_valid_closed_position(row)]


repository = PaperRepository()


def load_valid_closed_positions(limit: int = 1000, *, ascending: bool = False) -> list[dict[str, Any]]:
    return repository.valid_closed_positions(limit, ascending=ascending)
