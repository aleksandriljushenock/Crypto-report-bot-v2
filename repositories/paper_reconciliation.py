"""Paper account reconciliation after deploys/restarts.

Rebuilds account aggregates from valid execution history without touching strategy
signals. This is intentionally explicit/dry-run by default.
"""
from __future__ import annotations
from typing import Any
from repositories.paper_repository import repository


def compute(account_id: str = "main") -> dict[str, Any]:
    account = repository.account(account_id) or {}
    initial = float(account.get("initial_balance") or 100.0)
    valid_positions = repository.all_valid_closed_positions(None, ascending=True)
    trades = [repository._trade_from_closed_position(x) for x in valid_positions]
    open_positions = repository.positions_by_status("open", "opened_at")

    realized = sum(float(t.get("net_pnl") or 0) for t in trades)
    closed_fees = sum(float(t.get("fees") or 0) for t in trades)
    open_margin = sum(float(p.get("margin_usd") or 0) for p in open_positions)
    open_entry_fees = sum(float(p.get("entry_fee") or 0) for p in open_positions)
    expected_balance = initial + realized - open_margin - open_entry_fees
    expected_equity = initial + realized - open_entry_fees
    expected_fees = closed_fees + open_entry_fees
    return {
        "account_id": account_id,
        "initial_balance": round(initial, 8),
        "valid_closed_trades": len(trades),
        "open_positions": len(open_positions),
        "realized_pnl": round(realized, 8),
        "fees_paid": round(expected_fees, 8),
        "balance": round(expected_balance, 8),
        "equity": round(expected_equity, 8),
        "current": {
            "balance": float(account.get("balance") or 0),
            "equity": float(account.get("equity") or 0),
            "realized_pnl": float(account.get("realized_pnl") or 0),
            "fees_paid": float(account.get("fees_paid") or 0),
        },
    }


def reconcile(account_id: str = "main", *, apply: bool = False) -> dict[str, Any]:
    result = compute(account_id)
    if not apply:
        result["applied"] = False
        return result
    repository.update_account(account_id, {
        "balance": result["balance"],
        "equity": result["equity"],
        "realized_pnl": result["realized_pnl"],
        "fees_paid": result["fees_paid"],
    })
    result["applied"] = True
    return result
