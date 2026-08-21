"""Atomic Paper account reconciliation."""
from __future__ import annotations
from typing import Any
from repositories.paper_repository import repository


def compute(account_id: str = "main") -> dict[str, Any]:
    # Dry-run remains useful for diagnostics, but applies no writes.
    account = repository.account(account_id) or {}
    initial = float(account.get("initial_balance") or 100.0)
    valid_positions = repository.all_valid_closed_positions(None, ascending=True)
    trades = [repository._trade_from_closed_position(x) for x in valid_positions]
    open_positions = repository.positions_by_status("open", "opened_at")
    realized = sum(float(t.get("net_pnl") or 0) for t in trades)
    closed_fees = sum(float(t.get("fees") or 0) for t in trades)
    open_margin = sum(float(p.get("margin_usd") or 0) for p in open_positions)
    open_entry_fees = sum(float(p.get("entry_fee") or 0) for p in open_positions)
    return {"account_id": account_id, "initial_balance": initial, "valid_closed_trades": len(trades),
            "open_positions": len(open_positions), "realized_pnl": realized,
            "fees_paid": closed_fees + open_entry_fees,
            "balance": initial + realized - open_margin - open_entry_fees,
            "equity": initial + realized - open_entry_fees,
            "current": account}


def reconcile(account_id: str = "main", *, apply: bool = False) -> dict[str, Any]:
    if apply:
        return repository.reconcile_atomic(account_id)
    result = compute(account_id)
    result["applied"] = False
    return result
