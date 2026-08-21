"""Durable Supabase-backed paper trading for final Telegram signals.

The engine uses isolated-margin style accounting for simulation only. Estimated
liquidation prices are conservative approximations, not exchange guarantees.
"""
from __future__ import annotations

import math
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from core.logging_setup import get_logger
from core.runtime_config import boolean, integer, number
from core.events import emit
from repositories.paper_repository import repository as paper_repo
from trade_market_client import create_trade_market_client

log = get_logger("paper_trading")
ACCOUNT_ID = "main"
_PAPER_LOCK = threading.RLock()
_LAST_RECONCILE_AT: Optional[datetime] = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime] = None) -> str:
    return (dt or _now()).isoformat()


def _bool(name: str, default: bool) -> bool:
    return boolean(name, default)


def _float(name: str, default: float) -> float:
    return number(name, default)


def _int(name: str, default: int) -> int:
    return integer(name, default)

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


def _side(direction: Any) -> Optional[str]:
    value = str(direction or "").strip().upper()
    if value in {"LONG", "BUY"} or value.startswith("LONG "):
        return "LONG"
    if value in {"SHORT", "SELL"} or value.startswith("SHORT "):
        return "SHORT"
    return None


def ensure_account() -> dict[str, Any]:
    initial = max(1.0, _float("PAPER_INITIAL_BALANCE_USD", 100.0))
    try:
        existing = paper_repo.account(ACCOUNT_ID)
        if existing:
            return existing
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
        return paper_repo.insert_account(row)
    except Exception as exc:
        log.exception("Paper account initialization failed")
        raise RuntimeError("Paper account unavailable; execution is fail-closed") from exc


def get_account() -> dict[str, Any]:
    return ensure_account()


def _open_positions() -> list[dict[str, Any]]:
    try:
        return paper_repo.positions_by_status("open", "opened_at")
    except Exception:
        log.exception("Paper positions load failed")
        return []


def get_open_positions() -> list[dict[str, Any]]:
    return _open_positions()


def _pending_positions() -> list[dict[str, Any]]:
    try:
        return paper_repo.positions_by_status("pending_entry", "created_at")
    except Exception:
        log.exception("Paper pending positions load failed")
        return []


def get_pending_positions() -> list[dict[str, Any]]:
    return _pending_positions()


def get_recent_trades(limit: int = 20) -> list[dict[str, Any]]:
    try:
        return paper_repo.recent_trades(limit)
    except Exception:
        log.exception("Paper trade history load failed")
        return []


def _repair_paper_state(*, force_reconcile: bool = False) -> dict[str, Any]:
    """Heal split-write failures between positions, trade ledger and account.

    A closed position is canonical. If a worker dies between the CAS close and
    paper_trades/account updates, this routine backfills the ledger and
    periodically rebuilds account aggregates. Read-side statistics also merge
    closed positions, so UI remains correct even before persistence repair.
    """
    global _LAST_RECONCILE_AT
    if not _bool("PAPER_LEDGER_REPAIR_ENABLED", True):
        return {"status": "disabled", "repaired": 0, "errors": []}
    try:
        backfill = paper_repo.backfill_missing_trades(max(100, _int("PAPER_LEDGER_REPAIR_LIMIT", 2000)))
    except Exception as exc:
        log.warning("Paper ledger repair failed: %s", exc)
        return {"status": "error", "repaired": 0, "errors": [f"{type(exc).__name__}: {exc}"]}

    interval = max(1, _int("PAPER_RECONCILE_INTERVAL_MINUTES", 5))
    due = _LAST_RECONCILE_AT is None or (_now() - _LAST_RECONCILE_AT) >= timedelta(minutes=interval)
    if force_reconcile or backfill.get("repaired") or due:
        try:
            from repositories.paper_reconciliation import reconcile
            with _PAPER_LOCK:
                reconciliation = reconcile(ACCOUNT_ID, apply=True)
            _LAST_RECONCILE_AT = _now()
            backfill["reconciliation"] = reconciliation
        except Exception as exc:
            log.warning("Paper account reconciliation failed: %s", exc)
            backfill.setdefault("errors", []).append(f"reconcile: {type(exc).__name__}: {exc}")
    backfill["status"] = "ok" if not backfill.get("errors") else "partial"
    return backfill


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


def _current_market_price(client: Any, symbol: str) -> float:
    ticker = client.ticker_24h(symbol) or {}
    for key in ("lastPrice", "last_price", "price", "markPrice", "mark_price"):
        try:
            value = float(ticker.get(key) or 0)
            if value > 0:
                return value
        except Exception:
            pass
    return 0.0


def _execution_klines(client: Any, symbol: str, *, lookback_hours: Optional[float] = None) -> tuple[list[Any], str, int]:
    """Load execution candles while guaranteeing configured hold-window coverage.

    Exchange clients expose at most 1000 candles. Use 1m only when 1000 bars cover
    the required lookback; otherwise switch to 5m so a restart cannot create a
    silent hole in a 72h Paper position.
    """
    limit = max(100, min(1000, _int("PAPER_EXECUTION_KLINE_LIMIT", 1000)))
    hours = float(lookback_hours if lookback_hours is not None else max(_int("PAPER_MAX_HOLD_HOURS", 72), _int("PAPER_ENTRY_MAX_WAIT_HOURS", 12)))
    need_minutes = max(1.0, hours * 60.0 + 5.0)
    if need_minutes <= limit:
        try:
            rows = client.klines(symbol, "1m", limit) or []
            if rows:
                return rows, "1m", 1
        except Exception as exc:
            log.debug("Paper 1m klines unavailable for %s: %s", symbol, exc)
    rows = client.klines(symbol, "5m", limit) or []
    return rows, "5m", 5

def _iter_execution_candles(rows: list[Any], *, since: datetime, interval_minutes: int, not_before: Optional[datetime] = None, not_after: Optional[datetime] = None) -> list[tuple[datetime, float, float, float, float, bool, bool]]:
    """Return candles intersecting the legal event-time window.

    Boundary candles are retained, but marked partial. Callers must never use
    their full high/low because those extrema may have occurred before entry or
    after expiry. For a partial boundary we only trust the close when the close
    timestamp itself lies inside the legal window.
    """
    lower = max(since, not_before or since)
    out = []
    for item in rows or []:
        try:
            candle_start = datetime.fromtimestamp(float(item[0]) / 1000.0, tz=timezone.utc)
            candle_end = datetime.fromtimestamp(float(item[6]) / 1000.0, tz=timezone.utc) if len(item) > 6 and item[6] is not None else candle_start + timedelta(minutes=interval_minutes)
            if candle_end <= lower:
                continue
            if not_after is not None and candle_start >= not_after:
                continue
            partial_start = candle_start < lower
            partial_end = bool(not_after is not None and candle_end > not_after)
            open_price = float(item[1]); high = float(item[2]); low = float(item[3]); close = float(item[4])
            out.append((candle_start, open_price, high, low, close, partial_start, partial_end))
        except Exception:
            continue
    out.sort(key=lambda x: x[0])
    return out

def _liquidation_hit(side: str, liquidation: float, *, open_price: float, high: float, low: float) -> tuple[bool, bool]:
    if liquidation <= 0:
        return False, False
    if side == "LONG":
        return low <= liquidation, open_price <= liquidation
    return high >= liquidation, open_price >= liquidation


def _fill_pending_position(position: dict[str, Any], fill_price: float, fill_source: str = "trigger", execution_provider: Optional[str] = None, filled_at: Optional[datetime] = None) -> dict[str, Any]:
    if fill_price <= 0:
        return {"status": "invalid-fill"}
    signal = position.get("signal_payload") or {}
    side = str(position.get("side") or "")
    if side not in {"LONG", "SHORT"}:
        return {"status": "invalid-direction"}
    stop = float(position.get("stop_price") or 0); tp1 = float(position.get("tp1_price") or 0)
    if (side == "LONG" and not (stop < fill_price < tp1)) or (side == "SHORT" and not (tp1 < fill_price < stop)):
        return {"status": "invalid-geometry"}
    leverage, liquidation, stop_distance_pct, liquidation_buffer_pct = _leverage_and_liquidation(fill_price, stop, side)
    requested_margin = max(0.0, float(signal.get("suggestedPositionSizeUsd") or _float("POSITION_SIZE_BASE_USD", 3.0)))
    reserve = max(0.0, _float("PAPER_MIN_FREE_BALANCE_USD", 5.0))
    fee_rate = max(0.0, _float("PAPER_FEE_PCT_PER_SIDE", 0.06) / 100.0)
    fill_dt = filled_at or _now(); now = _iso(fill_dt)
    try:
        with _PAPER_LOCK:
            updated = paper_repo.fill_pending_atomic(
                position_id=position.get("id"), fill_price=fill_price, leverage=leverage,
                liquidation=liquidation, stop_distance_pct=stop_distance_pct,
                liquidation_buffer_pct=liquidation_buffer_pct, requested_margin=requested_margin,
                reserve=reserve, fee_rate=fee_rate, fill_source=fill_source,
                filled_at=now, max_hold_hours=max(1, _int("PAPER_MAX_HOLD_HOURS", 72)),
                execution_provider=execution_provider,
            )
        if not updated:
            return {"status": "already-processed-or-insufficient-balance"}
        emit("PAPER_FILLED", symbol=position.get("symbol"), fingerprint=position.get("fingerprint"), side=side, fill_price=fill_price, fill_source=fill_source)
        return {"status": "opened", "position": updated}
    except Exception:
        log.exception("Paper pending fill failed: %s", position.get("fingerprint"))
        return {"status": "error"}

def open_from_signal(signal: dict[str, Any], source: str = "signal") -> dict[str, Any]:
    """Register a paper order. A signal is not a fill.

    Pullbacks wait for the calculated entryPrice (midpoint of the displayed zone).
    Breakouts wait for the trigger. Only a market price already close enough to a
    breakout trigger may be filled immediately; otherwise it is marked missed.
    """
    if not _bool("PAPER_TRADING_ENABLED", True):
        return {"status": "disabled"}
    fingerprint = str(signal.get("fingerprint") or "").strip()
    symbol = str(signal.get("symbol") or "").upper()
    target_entry = _entry(signal)
    try:
        stop = float(signal.get("stop") or signal.get("stop_loss") or 0)
        tp1 = float(signal.get("tp1") or signal.get("target_price") or 0)
    except Exception:
        stop, tp1 = 0.0, 0.0
    if not fingerprint or not symbol or not target_entry or stop <= 0 or tp1 <= 0:
        return {"status": "invalid-signal"}
    side = _side(signal.get("direction") or signal.get("signal_direction"))
    if side is None:
        return {"status": "invalid-direction"}
    if (side == "LONG" and not (stop < target_entry < tp1)) or (side == "SHORT" and not (tp1 < target_entry < stop)):
        return {"status": "invalid-geometry"}

    try:
        existing = paper_repo.position_by_fingerprint(fingerprint)
        if existing:
            return {"status": "duplicate", "position": existing}
    except Exception:
        log.exception("Paper dedupe lookup failed")
        return {"status": "error"}

    setup = str(signal.get("setup") or "").upper()
    zone_low = zone_high = None
    text = str(signal.get("entryText") or "")
    if "–" in text:
        try:
            a, b = text.replace(">", "").split("–", 1)
            zone_low, zone_high = sorted((float(a.strip()), float(b.strip())))
        except Exception:
            pass
    now_dt = _now()
    now = _iso(now_dt)
    wait_hours = max(1, _int("PAPER_ENTRY_MAX_WAIT_HOURS", 12))
    row = {
        "account_id": ACCOUNT_ID, "fingerprint": fingerprint, "symbol": symbol, "side": side,
        "status": "pending_entry", "source": source, "entry_price": target_entry, "stop_price": stop,
        "tp1_price": tp1, "tp2_price": signal.get("tp2"), "tp3_price": signal.get("tp3"),
        "margin_usd": 0.0, "leverage": 1, "notional_usd": 0.0, "quantity": 0.0,
        "entry_fee": 0.0, "quality_score": signal.get("qualityScore"),
        "probability": signal.get("calibratedProbability") or signal.get("probability"),
        "expected_value_pct": signal.get("expectedValuePct"),
        "strategy_version": signal.get("hedgeProfileVersion") or "adaptive-profit-v8",
        "signal_payload": signal, "signal_entry_price": target_entry, "entry_zone_low": zone_low,
        "entry_zone_high": zone_high, "trigger_price": target_entry if setup == "BREAKOUT" else None,
        "pending_until": (now_dt + timedelta(hours=wait_hours)).isoformat(),
        "pending_reason": "WAIT_PULLBACK" if setup == "PULLBACK" else "WAIT_BREAKOUT",
        "opened_at": now, "last_checked_at": now, "created_at": now, "updated_at": now,
        "execution_audit": {
            "signal_price": signal.get("marketPriceAtSignal") or signal.get("market_price_at_signal"),
            "target_entry": target_entry, "signal_at": now, "source": source, "setup": setup,
        },
    }
    try:
        with _PAPER_LOCK:
            position = paper_repo.create_pending_atomic(
                row,
                max_active=max(1, _int("PAPER_MAX_OPEN_POSITIONS", 10)),
                one_per_symbol=_bool("PAPER_ONE_POSITION_PER_SYMBOL", True),
            )
        if not position:
            return {"status": "max-open-positions-or-symbol-busy"}
        emit("PAPER_PENDING", symbol=symbol, fingerprint=fingerprint, side=side, target_entry=target_entry, source=source)
    except Exception:
        log.exception("Paper pending order create failed: %s", fingerprint)
        return {"status": "error"}

    # Immediate execution is allowed only when the actual market price validates it.
    try:
        entry_client = create_trade_market_client()
        market = _current_market_price(entry_client, symbol)
        execution_provider = getattr(entry_client, "last_provider", None)
    except Exception:
        market = 0.0
    if market <= 0:
        return {"status": "pending_entry", "position": position}

    if setup == "PULLBACK":
        # A pullback entry is a limit-like target. If market is already beyond it in
        # the profitable direction, wait for a real retrace instead of phantom filling.
        touched = market <= target_entry if side == "LONG" else market >= target_entry
        adverse_beyond = market <= stop if side == "LONG" else market >= stop
        if touched and not adverse_beyond:
            return _fill_pending_position(position, target_entry, "pullback_limit_touch", execution_provider=execution_provider)
        return {"status": "pending_entry", "position": position, "market_price": market}

    if setup == "BREAKOUT":
        crossed = market >= target_entry if side == "LONG" else market <= target_entry
        if not crossed:
            return {"status": "pending_entry", "position": position, "market_price": market}
        deviation = abs(market - target_entry) / target_entry * 100.0
        max_dev = max(0.0, _float("PAPER_MAX_ENTRY_DEVIATION_PCT", 0.50))
        if deviation > max_dev:
            try:
                paper_repo.update_position(position.get("id"), {
                    "status": "cancelled", "close_reason": "MISSED_BREAKOUT",
                    "pending_reason": f"deviation={deviation:.4f}%", "updated_at": _iso(), "closed_at": _iso(),
                })
            except Exception:
                pass
            return {"status": "missed_entry", "position": position, "market_price": market}
        slip = max(0.0, _float("PAPER_ENTRY_SLIPPAGE_PCT", 0.03) / 100.0)
        fill = market * (1.0 + slip if side == "LONG" else 1.0 - slip)
        return _fill_pending_position(position, fill, "breakout_market", execution_provider=execution_provider)

    return {"status": "pending_entry", "position": position, "market_price": market}

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
    is_liquidation = str(reason or "").upper().startswith("LIQUIDATION")

    # Isolated-margin liquidation cannot lose more than the reserved margin
    # (entry fee has already left the account). Do not apply extra close
    # slippage/fee beyond the estimated liquidation point; otherwise Paper can
    # create impossible losses larger than isolated collateral.
    if is_liquidation:
        adjusted_exit = float(position.get("estimated_liquidation_price") or exit_price or 0)
        gross_pnl = -margin
        exit_fee = 0.0
        net_pnl = -margin - entry_fee
        released = 0.0
    else:
        adjusted_exit = exit_price * (1.0 - slippage if side == "LONG" else 1.0 + slippage)
        gross_pnl = notional * _signed_return(side, entry, adjusted_exit)
        exit_fee = notional * fee_rate
        net_pnl = gross_pnl - entry_fee - exit_fee
        released = max(0.0, margin + gross_pnl - exit_fee)

    now = closed_at or _iso()
    audit = dict(position.get("execution_audit") or {})
    audit.update({
        "exit_reason": reason,
        "exit_price": adjusted_exit,
        "closed_at": now,
    })
    if is_liquidation:
        audit.update({
            "liquidation_breached": True,
            "liquidation_hit_at": now,
            "liquidation_hit_price": float(position.get("estimated_liquidation_price") or exit_price or 0),
        })

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
    # V39 closes the position, writes the ledger and updates account aggregates
    # in one PostgreSQL transaction. This removes cross-process lost updates.
    equity_delta = -margin if is_liquidation else (gross_pnl - exit_fee)
    try:
        with _PAPER_LOCK:
            result = paper_repo.close_atomic(
                position_id=position.get("id"), exit_price=adjusted_exit, reason=reason,
                gross_pnl=gross_pnl, net_pnl=net_pnl, exit_fee=exit_fee, released=released,
                equity_delta=equity_delta, closed_at=now, execution_audit=audit, trade=trade,
            )
    except Exception:
        log.exception("Paper atomic close failed: %s", position.get("id"))
        return {}
    if not result:
        return {}
    trade["balance_after"] = float(result.get("balance_after") or 0)
    emit("PAPER_LIQUIDATED" if is_liquidation else "POSITION_CLOSED", symbol=position.get("symbol"), fingerprint=position.get("fingerprint"), reason=reason, net_pnl=net_pnl)
    return trade


def _parse_ts(value: Any) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def update_positions(notifier: Optional[Callable[[str], None]] = None) -> dict[str, Any]:
    # Disabling Paper Trading stops new entries but existing/pending orders must
    # continue to be reconciled. Pending orders never reserve margin until filled.
    # Heal any previous split-write before processing the next market tick.
    repair = _repair_paper_state()
    client = create_trade_market_client()
    pending = _pending_positions()
    pending_filled = 0
    pending_cancelled = 0
    pending_errors: list[str] = []
    for position in pending:
        try:
            target = float(position.get("signal_entry_price") or position.get("entry_price") or 0)
            if target <= 0:
                continue
            side = str(position.get("side") or "LONG")
            setup = str((position.get("signal_payload") or {}).get("setup") or "").upper()
            since = _parse_ts(position.get("last_checked_at") or position.get("created_at"))
            pending_until = _parse_ts(position.get("pending_until")) if position.get("pending_until") else None
            rows, _, interval_minutes = _execution_klines(client, position["symbol"])
            touched = False
            touch_time = None
            for ts, open_price, high, low, close, partial_start, partial_end in _iter_execution_candles(rows, since=since, interval_minutes=interval_minutes, not_before=_parse_ts(position.get("created_at")), not_after=pending_until):
                if partial_start or partial_end:
                    # OHLC extrema are temporally ambiguous on a boundary candle.
                    # Only its close is safe if that close belongs to the legal window.
                    effective_low = effective_high = close
                else:
                    effective_low, effective_high = low, high
                if setup == "PULLBACK":
                    touched = effective_low <= target if side == "LONG" else effective_high >= target
                else:
                    touched = effective_high >= target if side == "LONG" else effective_low <= target
                if touched:
                    touch_time = max(ts, _parse_ts(position.get("created_at")))
                    break
            if touched:
                fill = target
                if setup == "BREAKOUT":
                    slip = max(0.0, _float("PAPER_ENTRY_SLIPPAGE_PCT", 0.03) / 100.0)
                    fill = target * (1.0 + slip if side == "LONG" else 1.0 - slip)
                result = _fill_pending_position(position, fill, "candle_trigger", execution_provider=getattr(client, "last_provider", None), filled_at=touch_time)
                if result.get("status") == "opened":
                    pending_filled += 1
                    if notifier:
                        notifier(format_open_message(result))
                    continue
            if pending_until and _now() >= pending_until:
                paper_repo.update_position(position.get("id"), {
                    "status": "cancelled", "close_reason": "ENTRY_EXPIRED", "pending_reason": "price_never_reached_entry",
                    "closed_at": _iso(), "last_checked_at": _iso(), "updated_at": _iso(),
                }, expected_status="pending_entry")
                emit("PAPER_ENTRY_CANCELLED", symbol=position.get("symbol"), fingerprint=position.get("fingerprint"), reason="ENTRY_EXPIRED")
                pending_cancelled += 1
                if notifier:
                    notifier(format_missed_message(position, "ENTRY_EXPIRED"))
            else:
                paper_repo.update_position(position.get("id"), {"last_checked_at": _iso(), "updated_at": _iso()}, expected_status="pending_entry")
        except Exception as exc:
            text = f"{position.get('symbol')}: {type(exc).__name__}: {exc}"
            pending_errors.append(text)
            log.warning("Paper pending update failed: %s", text)

    positions = _open_positions()
    if not positions:
        return {"status": "ok", "checked": 0, "closed": 0, "liquidated": 0, "pending_checked": len(pending), "pending_filled": pending_filled, "pending_cancelled": pending_cancelled, "ledger_repaired": int(repair.get("repaired") or 0), "errors": pending_errors + list(repair.get("errors") or [])}
    closed: list[dict[str, Any]] = []
    errors: list[str] = []
    liquidation_count = 0
    for position in positions:
        try:
            last_checked = _parse_ts(position.get("last_checked_at") or position.get("opened_at"))
            opened_at = _parse_ts(position.get("opened_at"))
            max_hold = _parse_ts(position.get("max_hold_until")) if position.get("max_hold_until") else opened_at + timedelta(hours=max(1, _int("PAPER_MAX_HOLD_HOURS", 72)))
            provider = str(position.get("execution_provider") or "").strip()
            position_client = create_trade_market_client(providers=[provider]) if provider else client
            rows, _, interval_minutes = _execution_klines(position_client, position["symbol"])
            if not provider:
                provider = str(getattr(position_client, "last_provider", None) or "").strip()
                if provider:
                    paper_repo.update_position(position.get("id"), {"execution_provider": provider, "updated_at": _iso()}, expected_status="open")
                    position_client = create_trade_market_client(providers=[provider])
            candles = _iter_execution_candles(rows, since=last_checked, interval_minutes=interval_minutes, not_before=opened_at, not_after=max_hold)
            side = str(position.get("side") or "LONG")
            stop = float(position.get("stop_price") or 0)
            tp = float(position.get("tp1_price") or 0)
            liquidation = float(position.get("estimated_liquidation_price") or 0)
            reason = None
            exit_price = None
            exit_time = None

            # Historical events are authoritative. Process them chronologically
            # before considering the current ticker, otherwise a later TP can hide
            # an earlier SL/liquidation after downtime.
            for ts, open_price, high, low, close, partial_start, partial_end in candles:
                if partial_start or partial_end:
                    # Never attribute an unknown intra-candle wick to the legal window.
                    # The close is the only timestamp-safe observation available from OHLC.
                    safe_open = safe_high = safe_low = close
                else:
                    safe_open, safe_high, safe_low = open_price, high, low
                liq_hit, opened_beyond_liq = _liquidation_hit(side, liquidation, open_price=safe_open, high=safe_high, low=safe_low)
                stop_hit = safe_low <= stop if side == "LONG" else safe_high >= stop
                tp_hit = safe_high >= tp if side == "LONG" else safe_low <= tp
                if liq_hit:
                    reason = "LIQUIDATION_GAP" if opened_beyond_liq else "LIQUIDATION_CONSERVATIVE"
                    exit_price, exit_time = liquidation, ts.isoformat(); break
                if stop_hit and tp_hit:
                    reason, exit_price, exit_time = "SL_CONSERVATIVE", stop, ts.isoformat(); break
                if stop_hit:
                    reason, exit_price, exit_time = "SL", stop, ts.isoformat(); break
                if tp_hit:
                    reason, exit_price, exit_time = "TP1", tp, ts.isoformat(); break

            market = 0.0
            if reason is None and _now() < max_hold:
                try:
                    market = _current_market_price(position_client, position["symbol"])
                except Exception:
                    market = 0.0
                if market > 0 and liquidation > 0 and (market <= liquidation if side == "LONG" else market >= liquidation):
                    reason, exit_price, exit_time = "LIQUIDATION", liquidation, _iso()
                elif market > 0 and (market <= stop if side == "LONG" else market >= stop):
                    reason, exit_price, exit_time = "SL", stop, _iso()
                elif market > 0 and (market >= tp if side == "LONG" else market <= tp):
                    reason, exit_price, exit_time = "TP1", tp, _iso()

            if reason is None and _now() >= max_hold:
                # TIME_EXIT is priced at/just before the deadline, never at a later
                # current price. The last legal candle close is the conservative proxy.
                legal = [c for c in candles if c[0] < max_hold]
                exit_price = legal[-1][4] if legal else float(position.get("entry_price") or 0)
                reason, exit_time = "TIME_EXIT", max_hold.isoformat()
            if reason and exit_price and exit_price > 0:
                trade = _close_position(position, exit_price, reason, exit_time)
                if trade:
                    closed.append(trade)
                    if str(reason).startswith("LIQUIDATION"):
                        liquidation_count += 1
                    if notifier:
                        notifier(format_close_message(trade))
            else:
                paper_repo.update_position(position.get("id"), {"last_checked_at": _iso(), "updated_at": _iso()}, expected_status="open")
        except Exception as exc:
            text = f"{position.get('symbol')}: {type(exc).__name__}: {exc}"
            errors.append(text)
            log.warning("Paper position update failed: %s", text)
    return {"status": "ok", "checked": len(positions), "closed": len(closed), "liquidated": liquidation_count, "trades": closed, "pending_checked": len(pending), "pending_filled": pending_filled, "pending_cancelled": pending_cancelled, "ledger_repaired": int(repair.get("repaired") or 0), "errors": pending_errors + errors + list(repair.get("errors") or [])}


def performance() -> dict[str, Any]:
    account = ensure_account()
    try:
        stats_cap = _int("PAPER_STATS_MAX_TRADES", 0)
        trades = paper_repo.all_closed_trades(max_rows=stats_cap if stats_cap > 0 else None)
    except Exception:
        log.debug("Lifetime Paper history unavailable; falling back to recent ledger", exc_info=True)
        stats_cap = _int("PAPER_STATS_MAX_TRADES", 100000)
        trades = get_recent_trades(max(1000, stats_cap if stats_cap > 0 else 100000))
    positions = _open_positions()
    pending = _pending_positions()
    pnls = [float(t.get("net_pnl") or 0) for t in trades]
    eps = 1e-9
    wins = [x for x in pnls if x > eps]
    losses = [x for x in pnls if x < -eps]
    breakeven = [x for x in pnls if abs(x) <= eps]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    net_pnl = sum(pnls)
    initial = float(account.get("initial_balance") or 0)
    open_margin = sum(float(p.get("margin_usd") or 0) for p in positions)
    open_entry_fees = sum(float(p.get("entry_fee") or 0) for p in positions)
    realized_equity = initial + net_pnl - open_entry_fees
    unrealized_pnl = 0.0
    mark_prices = {}
    try:
        mtm_client = create_trade_market_client()
        fee_rate = max(0.0, _float("PAPER_FEE_PCT_PER_SIDE", 0.06) / 100.0)
        for pos in positions:
            symbol = str(pos.get("symbol") or "").strip().upper()
            if not symbol or float(pos.get("entry_price") or 0) <= 0:
                continue
            provider = str(pos.get("execution_provider") or "").strip()
            pc = create_trade_market_client(providers=[provider]) if provider else mtm_client
            mark = _current_market_price(pc, symbol)
            if mark <= 0:
                continue
            mark_prices[str(pos.get("id"))] = mark
            notional = float(pos.get("notional_usd") or 0); entry = float(pos.get("entry_price") or 0)
            gross = notional * _signed_return(str(pos.get("side") or "LONG"), entry, mark) if entry > 0 else 0.0
            unrealized_pnl += gross - notional * fee_rate
    except Exception:
        log.debug("Paper MTM unavailable", exc_info=True)
    derived_equity = realized_equity + unrealized_pnl
    derived_free_balance = derived_equity - open_margin
    account_balance = float(account.get("balance") or 0)
    account_equity = float(account.get("equity") or 0)
    liquidations = [t for t in trades if str(t.get("close_reason") or "").upper().startswith("LIQUIDATION")]
    tp_closes = [t for t in trades if str(t.get("close_reason") or "").upper().startswith("TP")]
    sl_closes = [t for t in trades if str(t.get("close_reason") or "").upper().startswith("SL")]
    time_exits = [t for t in trades if str(t.get("close_reason") or "").upper() == "TIME_EXIT"]
    return {
        "account": account,
        "open_positions": positions,
        "pending_positions": pending,
        "trades": trades,
        "closed_count": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(breakeven),
        "win_rate": len(wins) / (len(wins) + len(losses)) * 100.0 if (wins or losses) else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0),
        "net_pnl": net_pnl,
        "roi_pct": (net_pnl / initial * 100.0) if initial > 0 else 0.0,
        "avg_pnl": (net_pnl / len(trades)) if trades else 0.0,
        "liquidations": len(liquidations),
        "tp_closes": len(tp_closes),
        "sl_closes": len(sl_closes),
        "time_exits": len(time_exits),
        "derived_equity": derived_equity,
        "realized_equity": realized_equity,
        "unrealized_pnl": unrealized_pnl,
        "mark_prices": mark_prices,
        "derived_free_balance": derived_free_balance,
        "accounting_drift_balance": account_balance - derived_free_balance,
        "accounting_drift_equity": account_equity - realized_equity,
    }


def reset_account(initial_balance: Optional[float] = None) -> dict[str, Any]:
    amount = max(1.0, float(initial_balance or _float("PAPER_INITIAL_BALANCE_USD", 100.0)))
    try:
        result = paper_repo.reset_atomic(ACCOUNT_ID, amount)
        if not result.get("ok"):
            return {"status": "open-positions-exist", "active": int(result.get("active") or 0)}
        return {"status": "reset", "balance": amount}
    except Exception:
        log.exception("Paper account reset failed")
        return {"status": "error"}

def format_pending_message(result: dict[str, Any]) -> str:
    p = result.get("position") or {}
    payload = p.get("signal_payload") or {}
    setup = str(payload.get("setup") or "").upper()
    target = float(p.get("signal_entry_price") or p.get("entry_price") or 0)
    market = float(result.get("market_price") or 0)
    return (
        "⏳ <b>PAPER WAITING FOR ENTRY</b>\n\n"
        f"{p.get('side')} <b>{p.get('symbol')}</b> · {setup or 'SETUP'}\n"
        f"Target entry: <code>{target:.8g}</code>\n"
        + (f"Market now: <code>{market:.8g}</code>\n" if market > 0 else "")
        + f"TP1: <code>{float(p.get('tp1_price') or 0):.8g}</code>\n"
        + f"SL: <code>{float(p.get('stop_price') or 0):.8g}</code>\n"
        + "Позиция <b>ещё не открыта</b>; маржа и комиссии не списаны."
    )


def format_missed_message(position: dict[str, Any], reason: str) -> str:
    return (
        "⌛ <b>PAPER ENTRY NOT FILLED</b>\n\n"
        f"{position.get('side')} <b>{position.get('symbol')}</b>\n"
        f"Причина: <b>{reason}</b>\n"
        "Сделка отмечена как несостоявшаяся и не входит в PnL/Win Rate."
    )


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
    reason = str(trade.get("close_reason") or "")
    icon = "💥" if reason.startswith("LIQUIDATION") else ("✅" if pnl > 0 else "❌")
    title = "PAPER POSITION LIQUIDATED" if reason.startswith("LIQUIDATION") else "PAPER POSITION CLOSED"
    return (
        f"{icon} <b>{title}</b>\n\n"
        f"{trade.get('side')} <b>{trade.get('symbol')}</b>\n"
        f"Reason: <b>{trade.get('close_reason')}</b>\n"
        f"PnL: <b>{pnl:+.4f} USDT</b>\n"
        f"Return on margin: <b>{float(trade.get('return_on_margin_pct') or 0):+.2f}%</b>\n"
        f"Fees: <b>${float(trade.get('fees') or 0):.4f}</b>\n"
        f"Balance: <b>${float(trade.get('balance_after') or 0):.2f}</b>"
    )
