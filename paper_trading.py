"""Durable Supabase-backed paper trading for final Telegram signals.

The engine uses isolated-margin style accounting for simulation only. Estimated
liquidation prices are conservative approximations, not exchange guarantees.
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from core.logging_setup import get_logger
from trade_market_client import create_trade_market_client

log = get_logger("paper_trading")
ACCOUNT_ID = "main"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime] = None) -> str:
    return (dt or _now()).isoformat()


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "да"}


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return float(default)


def _int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return int(default)


def _client():
    from cloud_client import get_supabase_client
    return get_supabase_client()


def _entry(signal: dict[str, Any]) -> Optional[float]:
    for key in ("entryPrice", "entry_price", "market_price_at_signal"):
        try:
            value = float(signal.get(key) or 0)
            if value > 0:
                return value
        except Exception:
            pass
    text = str(signal.get("entryText") or "").replace(">", "").strip()
    if "–" in text:
        try:
            a, b = text.split("–", 1)
            return (float(a) + float(b)) / 2
        except Exception:
            return None
    try:
        value = float(text)
        return value if value > 0 else None
    except Exception:
        return None


def _side(direction: Any) -> str:
    return "SHORT" if "SHORT" in str(direction or "").upper() else "LONG"


def ensure_account() -> dict[str, Any]:
    initial = max(1.0, _float("PAPER_INITIAL_BALANCE_USD", 100.0))
    try:
        response = _client().table("paper_accounts").select("*").eq("id", ACCOUNT_ID).limit(1).execute()
        if response.data:
            return response.data[0]
        row = {
            "id": ACCOUNT_ID,
            "initial_balance": initial,
            "balance": initial,
            "equity": initial,
            "realized_pnl": 0.0,
            "fees_paid": 0.0,
            "status": "active",
            "created_at": _iso(),
            "updated_at": _iso(),
        }
        response = _client().table("paper_accounts").insert(row).execute()
        return (response.data or [row])[0]
    except Exception:
        log.exception("Paper account initialization failed")
        return {
            "id": ACCOUNT_ID,
            "initial_balance": initial,
            "balance": initial,
            "equity": initial,
            "realized_pnl": 0.0,
            "fees_paid": 0.0,
            "status": "unavailable",
        }


def get_account() -> dict[str, Any]:
    return ensure_account()


def _open_positions() -> list[dict[str, Any]]:
    try:
        response = _client().table("paper_positions").select("*").eq("status", "open").order("opened_at").execute()
        return response.data or []
    except Exception:
        log.exception("Paper positions load failed")
        return []


def get_open_positions() -> list[dict[str, Any]]:
    return _open_positions()


def get_recent_trades(limit: int = 20) -> list[dict[str, Any]]:
    try:
        response = _client().table("paper_trades").select("*").order("closed_at", desc=True).limit(max(1, min(limit, 1000))).execute()
        return response.data or []
    except Exception:
        log.exception("Paper trade history load failed")
        return []


def _position_margin(signal: dict[str, Any], account: dict[str, Any]) -> float:
    suggested = signal.get("suggestedPositionSizeUsd")
    try:
        margin = float(suggested or 0)
    except Exception:
        margin = 0.0
    if margin <= 0:
        margin = _float("POSITION_SIZE_BASE_USD", 3.0)
    available = max(0.0, float(account.get("balance") or 0))
    reserve = max(0.0, _float("PAPER_MIN_FREE_BALANCE_USD", 5.0))
    return round(max(0.0, min(margin, available - reserve)), 8)


def _leverage_and_liquidation(entry: float, stop: float, side: str) -> tuple[int, float, float, float]:
    stop_distance = abs(entry - stop) / entry
    safety = max(0.0005, _float("PAPER_LIQUIDATION_BUFFER_PCT", 0.5) / 100.0)
    maintenance = max(0.0, _float("PAPER_MAINTENANCE_MARGIN_PCT", 0.5) / 100.0)
    max_lev = max(1, _int("PAPER_MAX_LEVERAGE", 20))
    # Conservative isolated-margin approximation. Lower leverage keeps liquidation
    # beyond stop by safety+maintenance distance.
    denominator = max(0.001, stop_distance + safety + maintenance)
    leverage = max(1, min(max_lev, int(math.floor(1.0 / denominator))))
    liquidation_distance = max(0.0, 1.0 / leverage - maintenance)
    liquidation = entry * (1.0 - liquidation_distance) if side == "LONG" else entry * (1.0 + liquidation_distance)
    buffer_pct = abs(stop - liquidation) / entry * 100.0
    return leverage, liquidation, stop_distance * 100.0, buffer_pct


def open_from_signal(signal: dict[str, Any], source: str = "signal") -> dict[str, Any]:
    if not _bool("PAPER_TRADING_ENABLED", True):
        return {"status": "disabled"}
    fingerprint = str(signal.get("fingerprint") or "").strip()
    symbol = str(signal.get("symbol") or "").upper()
    entry = _entry(signal)
    try:
        stop = float(signal.get("stop") or signal.get("stop_loss") or 0)
        tp1 = float(signal.get("tp1") or signal.get("target_price") or 0)
    except Exception:
        stop, tp1 = 0.0, 0.0
    if not fingerprint or not symbol or not entry or stop <= 0 or tp1 <= 0:
        return {"status": "invalid-signal"}
    side = _side(signal.get("direction") or signal.get("signal_direction"))
    if (side == "LONG" and not (stop < entry < tp1)) or (side == "SHORT" and not (tp1 < entry < stop)):
        return {"status": "invalid-geometry"}

    try:
        existing = _client().table("paper_positions").select("id,status").eq("fingerprint", fingerprint).limit(1).execute()
        if existing.data:
            return {"status": "duplicate", "position": existing.data[0]}
    except Exception:
        log.exception("Paper dedupe lookup failed")
        return {"status": "error"}

    positions = _open_positions()
    if len(positions) >= max(1, _int("PAPER_MAX_OPEN_POSITIONS", 10)):
        return {"status": "max-open-positions"}
    if any(str(p.get("symbol") or "").upper() == symbol for p in positions) and _bool("PAPER_ONE_POSITION_PER_SYMBOL", True):
        return {"status": "symbol-already-open"}

    account = ensure_account()
    margin = _position_margin(signal, account)
    if margin <= 0:
        return {"status": "insufficient-balance"}
    leverage, liquidation, stop_distance_pct, liquidation_buffer_pct = _leverage_and_liquidation(entry, stop, side)
    notional = margin * leverage
    fee_rate = max(0.0, _float("PAPER_FEE_PCT_PER_SIDE", 0.06) / 100.0)
    entry_fee = notional * fee_rate
    if margin + entry_fee > float(account.get("balance") or 0):
        return {"status": "insufficient-balance"}
    quantity = notional / entry
    now = _iso()
    row = {
        "account_id": ACCOUNT_ID,
        "fingerprint": fingerprint,
        "symbol": symbol,
        "side": side,
        "status": "open",
        "source": source,
        "entry_price": entry,
        "stop_price": stop,
        "tp1_price": tp1,
        "tp2_price": signal.get("tp2"),
        "tp3_price": signal.get("tp3"),
        "margin_usd": margin,
        "leverage": leverage,
        "notional_usd": notional,
        "quantity": quantity,
        "estimated_liquidation_price": liquidation,
        "stop_distance_pct": stop_distance_pct,
        "liquidation_buffer_pct": liquidation_buffer_pct,
        "entry_fee": entry_fee,
        "quality_score": signal.get("qualityScore"),
        "probability": signal.get("calibratedProbability") or signal.get("probability"),
        "expected_value_pct": signal.get("expectedValuePct"),
        "strategy_version": signal.get("hedgeProfileVersion") or "adaptive-profit-v8",
        "signal_payload": signal,
        "opened_at": now,
        "last_checked_at": now,
        "max_hold_until": (_now() + timedelta(hours=max(1, _int("PAPER_MAX_HOLD_HOURS", 72)))).isoformat(),
        "created_at": now,
        "updated_at": now,
    }
    try:
        response = _client().table("paper_positions").insert(row).execute()
        position = (response.data or [row])[0]
        new_balance = float(account.get("balance") or 0) - margin - entry_fee
        _client().table("paper_accounts").update({
            "balance": new_balance,
            "equity": float(account.get("equity") or account.get("balance") or 0) - entry_fee,
            "fees_paid": float(account.get("fees_paid") or 0) + entry_fee,
            "updated_at": now,
        }).eq("id", ACCOUNT_ID).execute()
        return {"status": "opened", "position": position}
    except Exception:
        log.exception("Paper position open failed: %s", fingerprint)
        return {"status": "error"}


def _signed_return(side: str, entry: float, exit_price: float) -> float:
    raw = (exit_price - entry) / entry
    return raw if side == "LONG" else -raw


def _close_position(position: dict[str, Any], exit_price: float, reason: str, closed_at: Optional[str] = None) -> dict[str, Any]:
    account = ensure_account()
    entry = float(position.get("entry_price") or 0)
    notional = float(position.get("notional_usd") or 0)
    margin = float(position.get("margin_usd") or 0)
    entry_fee = float(position.get("entry_fee") or 0)
    fee_rate = max(0.0, _float("PAPER_FEE_PCT_PER_SIDE", 0.06) / 100.0)
    slippage = max(0.0, _float("PAPER_SLIPPAGE_PCT", 0.03) / 100.0)
    side = str(position.get("side") or "LONG")
    adjusted_exit = exit_price * (1.0 - slippage if side == "LONG" else 1.0 + slippage)
    gross_pnl = notional * _signed_return(side, entry, adjusted_exit)
    exit_fee = notional * fee_rate
    net_pnl = gross_pnl - entry_fee - exit_fee
    released = max(0.0, margin + gross_pnl - exit_fee)
    now = closed_at or _iso()
    trade = {
        "account_id": ACCOUNT_ID,
        "position_id": position.get("id"),
        "fingerprint": position.get("fingerprint"),
        "symbol": position.get("symbol"),
        "side": side,
        "entry_price": entry,
        "exit_price": adjusted_exit,
        "stop_price": position.get("stop_price"),
        "target_price": position.get("tp1_price"),
        "margin_usd": margin,
        "leverage": position.get("leverage"),
        "notional_usd": notional,
        "gross_pnl": gross_pnl,
        "net_pnl": net_pnl,
        "return_on_margin_pct": (net_pnl / margin * 100.0) if margin else 0.0,
        "fees": entry_fee + exit_fee,
        "close_reason": reason,
        "quality_score": position.get("quality_score"),
        "probability": position.get("probability"),
        "expected_value_pct": position.get("expected_value_pct"),
        "strategy_version": position.get("strategy_version"),
        "opened_at": position.get("opened_at"),
        "closed_at": now,
        "created_at": now,
    }
    try:
        _client().table("paper_trades").insert(trade).execute()
        _client().table("paper_positions").update({
            "status": "closed",
            "exit_price": adjusted_exit,
            "close_reason": reason,
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "closed_at": now,
            "last_checked_at": now,
            "updated_at": now,
        }).eq("id", position.get("id")).execute()
        new_balance = float(account.get("balance") or 0) + released
        realized = float(account.get("realized_pnl") or 0) + net_pnl
        fees = float(account.get("fees_paid") or 0) + exit_fee
        _client().table("paper_accounts").update({
            "balance": new_balance,
            "equity": new_balance,
            "realized_pnl": realized,
            "fees_paid": fees,
            "updated_at": now,
        }).eq("id", ACCOUNT_ID).execute()
        trade["balance_after"] = new_balance
        return trade
    except Exception:
        log.exception("Paper position close failed: %s", position.get("id"))
        return {}


def _parse_ts(value: Any) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def update_positions(notifier: Optional[Callable[[str], None]] = None) -> dict[str, Any]:
    # Disabling Paper Trading stops new entries but existing positions must
    # continue to be reconciled until TP/SL/time exit.
    positions = _open_positions()
    if not positions:
        return {"status": "ok", "checked": 0, "closed": 0}
    client = create_trade_market_client()
    closed: list[dict[str, Any]] = []
    errors: list[str] = []
    for position in positions:
        try:
            opened = _parse_ts(position.get("last_checked_at") or position.get("opened_at"))
            rows = client.klines(position["symbol"], "5m", 1000) or []
            candles = []
            for item in rows:
                ts = datetime.fromtimestamp(float(item[0]) / 1000.0, tz=timezone.utc)
                if ts >= opened:
                    candles.append((ts, float(item[2]), float(item[3]), float(item[4])))
            side = str(position.get("side") or "LONG")
            stop = float(position.get("stop_price") or 0)
            tp = float(position.get("tp1_price") or 0)
            reason = None
            exit_price = None
            exit_time = None
            for ts, high, low, close in sorted(candles, key=lambda x: x[0]):
                stop_hit = low <= stop if side == "LONG" else high >= stop
                tp_hit = high >= tp if side == "LONG" else low <= tp
                if stop_hit and tp_hit:
                    # Conservative assumption when intrabar order is unknown.
                    reason, exit_price, exit_time = "SL_CONSERVATIVE", stop, ts.isoformat()
                    break
                if stop_hit:
                    reason, exit_price, exit_time = "SL", stop, ts.isoformat()
                    break
                if tp_hit:
                    reason, exit_price, exit_time = "TP1", tp, ts.isoformat()
                    break
            max_hold = _parse_ts(position.get("max_hold_until"))
            if reason is None and _now() >= max_hold:
                if candles:
                    exit_price = candles[-1][3]
                else:
                    ticker = client.ticker_24h(position["symbol"])
                    exit_price = float(ticker.get("lastPrice") or ticker.get("last_price") or 0)
                reason, exit_time = "TIME_EXIT", _iso()
            if reason and exit_price and exit_price > 0:
                trade = _close_position(position, exit_price, reason, exit_time)
                if trade:
                    closed.append(trade)
                    if notifier:
                        notifier(format_close_message(trade))
            else:
                _client().table("paper_positions").update({"last_checked_at": _iso(), "updated_at": _iso()}).eq("id", position.get("id")).execute()
        except Exception as exc:
            text = f"{position.get('symbol')}: {type(exc).__name__}: {exc}"
            errors.append(text)
            log.warning("Paper position update failed: %s", text)
    return {"status": "ok", "checked": len(positions), "closed": len(closed), "trades": closed, "errors": errors}


def performance() -> dict[str, Any]:
    account = ensure_account()
    trades = get_recent_trades(1000)
    positions = _open_positions()
    wins = [t for t in trades if float(t.get("net_pnl") or 0) > 0]
    losses = [t for t in trades if float(t.get("net_pnl") or 0) <= 0]
    gross_profit = sum(float(t.get("net_pnl") or 0) for t in wins)
    gross_loss = abs(sum(float(t.get("net_pnl") or 0) for t in losses))
    return {
        "account": account,
        "open_positions": positions,
        "trades": trades,
        "closed_count": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(trades) * 100.0 if trades else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0),
        "net_pnl": sum(float(t.get("net_pnl") or 0) for t in trades),
    }


def reset_account(initial_balance: Optional[float] = None) -> dict[str, Any]:
    if _open_positions():
        return {"status": "open-positions-exist"}
    amount = max(1.0, float(initial_balance or _float("PAPER_INITIAL_BALANCE_USD", 100.0)))
    try:
        _client().table("paper_trades").delete().eq("account_id", ACCOUNT_ID).execute()
        _client().table("paper_positions").delete().eq("account_id", ACCOUNT_ID).execute()
        now = _iso()
        row = {
            "initial_balance": amount,
            "balance": amount,
            "equity": amount,
            "realized_pnl": 0.0,
            "fees_paid": 0.0,
            "status": "active",
            "updated_at": now,
        }
        _client().table("paper_accounts").upsert({"id": ACCOUNT_ID, **row}, on_conflict="id").execute()
        return {"status": "reset", "balance": amount}
    except Exception:
        log.exception("Paper account reset failed")
        return {"status": "error"}


def format_open_message(result: dict[str, Any]) -> str:
    p = result.get("position") or {}
    return (
        "🧪 <b>PAPER POSITION OPENED</b>\n\n"
        f"{p.get('side')} <b>{p.get('symbol')}</b>\n"
        f"Entry: <code>{float(p.get('entry_price') or 0):.8g}</code>\n"
        f"TP1: <code>{float(p.get('tp1_price') or 0):.8g}</code>\n"
        f"SL: <code>{float(p.get('stop_price') or 0):.8g}</code>\n"
        f"Margin: <b>${float(p.get('margin_usd') or 0):.2f}</b>\n"
        f"Leverage: <b>{int(p.get('leverage') or 1)}x</b>\n"
        f"Notional: <b>${float(p.get('notional_usd') or 0):.2f}</b>\n"
        f"Est. liquidation: <code>{float(p.get('estimated_liquidation_price') or 0):.8g}</code>\n"
        f"Buffer after SL: <b>{float(p.get('liquidation_buffer_pct') or 0):.2f}%</b>"
    )


def format_close_message(trade: dict[str, Any]) -> str:
    pnl = float(trade.get("net_pnl") or 0)
    icon = "✅" if pnl > 0 else "❌"
    return (
        f"{icon} <b>PAPER POSITION CLOSED</b>\n\n"
        f"{trade.get('side')} <b>{trade.get('symbol')}</b>\n"
        f"Reason: <b>{trade.get('close_reason')}</b>\n"
        f"PnL: <b>{pnl:+.4f} USDT</b>\n"
        f"Return on margin: <b>{float(trade.get('return_on_margin_pct') or 0):+.2f}%</b>\n"
        f"Fees: <b>${float(trade.get('fees') or 0):.4f}</b>\n"
        f"Balance: <b>${float(trade.get('balance_after') or 0):.2f}</b>"
    )
