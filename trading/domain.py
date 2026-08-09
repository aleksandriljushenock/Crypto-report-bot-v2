"""Canonical trading lifecycle types."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any

class TradeState(str, Enum):
    SIGNAL = "signal"
    PENDING_ENTRY = "pending_entry"
    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    INVALID = "invalid"

@dataclass(frozen=True)
class ExecutionAudit:
    fingerprint: str
    symbol: str
    side: str
    signal_price: float | None
    target_entry: float
    actual_fill: float | None = None
    fill_source: str | None = None
    signal_at: str | None = None
    filled_at: str | None = None


def is_valid_closed_position(row: dict[str, Any]) -> bool:
    """Only actual fills may be used by Optimizer/Adaptive Model."""
    if str(row.get("status") or "").lower() != TradeState.CLOSED.value:
        return False
    if str(row.get("close_reason") or "").upper().startswith("INVALID_FILL"):
        return False
    try:
        entry = float(row.get("entry_price") or 0)
        margin = float(row.get("margin_usd") or 0)
        opened = row.get("opened_at")
        closed = row.get("closed_at")
        fill_source = str(row.get("fill_price_source") or "").strip()
        return entry > 0 and margin > 0 and bool(opened) and bool(closed) and bool(fill_source)
    except Exception:
        return False
