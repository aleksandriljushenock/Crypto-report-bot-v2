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

    def create_pending_atomic(self, row: dict[str, Any], *, max_active: int, one_per_symbol: bool) -> dict[str, Any] | None:
        data = _client().rpc("paper_create_pending_v38", {
            "p_row": row,
            "p_max_active": int(max_active),
            "p_one_per_symbol": bool(one_per_symbol),
        }).execute().data
        if isinstance(data, list):
            return data[0] if data else None
        return data or None

    def fill_pending_atomic(self, *, position_id: Any, fill_price: float, leverage: int, liquidation: float,
                            stop_distance_pct: float, liquidation_buffer_pct: float, requested_margin: float,
                            reserve: float, fee_rate: float, fill_source: str, filled_at: str,
                            max_hold_hours: int, execution_provider: str | None) -> dict[str, Any] | None:
        data = _client().rpc("paper_fill_pending_v38", {
            "p_position_id": str(position_id), "p_fill_price": float(fill_price), "p_leverage": int(leverage),
            "p_liquidation": float(liquidation), "p_stop_distance_pct": float(stop_distance_pct),
            "p_liquidation_buffer_pct": float(liquidation_buffer_pct), "p_requested_margin": float(requested_margin),
            "p_reserve": float(reserve), "p_fee_rate": float(fee_rate), "p_fill_source": fill_source,
            "p_filled_at": filled_at, "p_max_hold_hours": int(max_hold_hours),
            "p_execution_provider": execution_provider,
        }).execute().data
        if isinstance(data, list):
            return data[0] if data else None
        return data or None


    def close_atomic(self, *, position_id: Any, exit_price: float, reason: str, gross_pnl: float, net_pnl: float,
                     exit_fee: float, released: float, equity_delta: float, closed_at: str,
                     execution_audit: dict[str, Any], trade: dict[str, Any]) -> dict[str, Any] | None:
        data = _client().rpc("paper_close_v39", {
            "p_position_id": str(position_id), "p_exit_price": float(exit_price), "p_reason": reason,
            "p_gross_pnl": float(gross_pnl), "p_net_pnl": float(net_pnl), "p_exit_fee": float(exit_fee),
            "p_released": float(released), "p_equity_delta": float(equity_delta), "p_closed_at": closed_at,
            "p_execution_audit": execution_audit or {}, "p_trade": trade,
        }).execute().data
        if isinstance(data, list):
            return data[0] if data else None
        return data or None

    def void_execution_atomic(self, *, position_id: Any, reason: str, closed_at: str) -> dict[str, Any] | None:
        data = _client().rpc("paper_void_execution_v48", {"p_position_id": str(position_id), "p_reason": str(reason), "p_closed_at": closed_at}).execute().data
        if isinstance(data, list):
            return data[0] if data else None
        return data or None

    def reconcile_atomic(self, account_id: str) -> dict[str, Any]:
        data = _client().rpc("paper_reconcile_v48", {"p_account_id": account_id}).execute().data
        if isinstance(data, list):
            return data[0] if data else {}
        return data or {}

    def reset_atomic(self, account_id: str, initial_balance: float) -> dict[str, Any]:
        data = _client().rpc("paper_reset_v39", {"p_account_id": account_id, "p_initial_balance": float(initial_balance)}).execute().data
        if isinstance(data, list):
            return data[0] if data else {}
        return data or {}

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

        V38 uses a non-partial UNIQUE index on position_id, which PostgREST can
        safely use as an ON CONFLICT target.
        """
        _client().table("paper_trades").upsert(row, on_conflict="position_id").execute()

    @staticmethod
    def _trade_from_closed_position(row: dict[str, Any]) -> dict[str, Any]:
        """Build a durable trade-ledger row from the canonical closed position.

        paper_positions is the lifecycle source of truth. This fallback makes
        statistics resilient when the process dies after the position CAS-close
        but before paper_trades is written.
        """
        margin = float(row.get("margin_usd") or 0)
        net_pnl = float(row.get("net_pnl") or 0)
        gross_pnl = float(row.get("gross_pnl") or net_pnl)
        entry_fee = float(row.get("entry_fee") or 0)
        fees = max(0.0, gross_pnl - net_pnl)
        if fees <= 0 and entry_fee > 0:
            fees = entry_fee
        closed_at = row.get("closed_at") or row.get("updated_at") or row.get("opened_at")
        return {
            "account_id": row.get("account_id") or "main",
            "position_id": row.get("id"),
            "fingerprint": row.get("fingerprint"),
            "symbol": row.get("symbol"),
            "side": row.get("side"),
            "entry_price": float(row.get("entry_price") or 0),
            "exit_price": float(row.get("exit_price") or 0),
            "stop_price": row.get("stop_price"),
            "target_price": row.get("tp1_price"),
            "margin_usd": margin,
            "leverage": int(row.get("leverage") or 1),
            "notional_usd": float(row.get("notional_usd") or 0),
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "return_on_margin_pct": (net_pnl / margin * 100.0) if margin else 0.0,
            "fees": fees,
            "close_reason": row.get("close_reason") or "UNKNOWN",
            "quality_score": row.get("quality_score"),
            "probability": row.get("probability"),
            "expected_value_pct": row.get("expected_value_pct"),
            "strategy_version": row.get("strategy_version"),
            "opened_at": row.get("opened_at"),
            "closed_at": closed_at,
            "created_at": closed_at,
            "_synthetic_from_position": True,
        }

    def recent_trades(self, limit: int = 20, *, valid_only: bool = True) -> list[dict[str, Any]]:
        max_rows = max(1, min(limit, 5000))
        fetch_rows = max(max_rows * 3, 1000) if valid_only else max_rows
        fetch_rows = min(fetch_rows, 5000)
        trades = (_client().table("paper_trades").select("*").order("closed_at", desc=True)
                  .limit(fetch_rows).execute().data or [])
        if not valid_only:
            return trades[:max_rows]

        # Canonical source of truth is a real CLOSED position. Merge the ledger
        # with closed positions so one failed paper_trades write cannot erase a
        # finished deal from Telegram statistics or model training.
        positions = self.valid_closed_positions(fetch_rows, ascending=False)
        valid_fp = {str(row.get("fingerprint") or "") for row in positions if row.get("fingerprint")}
        valid_ids = {str(row.get("id") or "") for row in positions if row.get("id")}
        filtered = [row for row in trades if str(row.get("fingerprint") or "") in valid_fp or str(row.get("position_id") or "") in valid_ids]
        seen_ids = {str(row.get("position_id") or "") for row in filtered if row.get("position_id")}
        seen_fp = {str(row.get("fingerprint") or "") for row in filtered if row.get("fingerprint")}
        for position in positions:
            pid = str(position.get("id") or "")
            fp = str(position.get("fingerprint") or "")
            if (pid and pid in seen_ids) or (fp and fp in seen_fp):
                continue
            filtered.append(self._trade_from_closed_position(position))
        filtered.sort(key=lambda row: str(row.get("closed_at") or row.get("created_at") or ""), reverse=True)
        return filtered[:max_rows]

    def backfill_missing_trades(self, limit: int = 1000) -> dict[str, Any]:
        """Persist orphan CLOSED positions idempotently across the full history.

        ``limit`` is the maximum number of repairs per pass, not a history window.
        """
        positions = self.all_valid_closed_positions(None, ascending=True)
        ledger: list[dict[str, Any]] = []
        offset = 0
        while True:
            rows = (_client().table("paper_trades").select("position_id,fingerprint").range(offset, offset + 999).execute().data or [])
            ledger.extend(rows)
            if len(rows) < 1000:
                break
            offset += 1000
        seen_ids = {str(row.get("position_id") or "") for row in ledger if row.get("position_id")}
        seen_fp = {str(row.get("fingerprint") or "") for row in ledger if row.get("fingerprint")}
        repaired = 0; errors: list[str] = []
        repair_cap = max(1, int(limit or 1000))
        for position in positions:
            pid = str(position.get("id") or ""); fp = str(position.get("fingerprint") or "")
            if (pid and pid in seen_ids) or (fp and fp in seen_fp):
                continue
            try:
                row = self._trade_from_closed_position(position); row.pop("_synthetic_from_position", None)
                self.upsert_trade(row); repaired += 1
                if pid: seen_ids.add(pid)
                if fp: seen_fp.add(fp)
                if repaired >= repair_cap: break
            except Exception as exc:
                errors.append(f"{position.get('symbol') or pid}: {type(exc).__name__}: {exc}")
        return {"checked": len(positions), "repaired": repaired, "errors": errors}

    def delete_account_history(self, account_id: str) -> None:
        _client().table("paper_trades").delete().eq("account_id", account_id).execute()
        _client().table("paper_positions").delete().eq("account_id", account_id).execute()

    def valid_closed_positions(self, limit: int = 1000, *, ascending: bool = False) -> list[dict[str, Any]]:
        fields = (
            "id,account_id,fingerprint,symbol,side,status,entry_price,exit_price,stop_price,tp1_price,"
            "margin_usd,leverage,notional_usd,entry_fee,gross_pnl,net_pnl,quality_score,probability,"
            "expected_value_pct,signal_payload,opened_at,closed_at,updated_at,close_reason,strategy_version,fill_price_source,execution_provider,execution_verified"
        )
        rows = (_client().table("paper_positions").select(fields).eq("status", "closed")
                .order("closed_at", desc=not ascending).limit(max(1, min(limit, 5000))).execute().data or [])
        return [row for row in rows if is_valid_closed_position(row)]

    def all_valid_closed_positions(self, max_rows: int | None = None, *, ascending: bool = True, page_size: int = 1000) -> list[dict[str, Any]]:
        fields = (
            "id,account_id,fingerprint,symbol,side,status,entry_price,exit_price,stop_price,tp1_price,"
            "margin_usd,leverage,notional_usd,entry_fee,gross_pnl,net_pnl,quality_score,probability,"
            "expected_value_pct,signal_payload,opened_at,closed_at,updated_at,close_reason,strategy_version,fill_price_source,execution_provider,execution_verified"
        )
        out: list[dict[str, Any]] = []
        page_size = max(100, min(int(page_size), 1000))
        cap = max(1, int(max_rows)) if max_rows is not None and int(max_rows) > 0 else None
        offset = 0
        while cap is None or offset < cap:
            end = offset + page_size - 1 if cap is None else min(offset + page_size, cap) - 1
            rows = (_client().table("paper_positions").select(fields).eq("status", "closed")
                    .order("closed_at", desc=not ascending).range(offset, end).execute().data or [])
            if not rows:
                break
            out.extend(row for row in rows if is_valid_closed_position(row))
            if len(rows) < page_size:
                break
            offset += page_size
        return out

    def all_closed_trades(self, max_rows: int | None = None) -> list[dict[str, Any]]:
        positions = self.all_valid_closed_positions(max_rows=max_rows, ascending=False)
        return [self._trade_from_closed_position(row) for row in positions]


repository = PaperRepository()


def load_valid_closed_positions(limit: int = 1000, *, ascending: bool = False) -> list[dict[str, Any]]:
    return repository.valid_closed_positions(limit, ascending=ascending)
