from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from core.runtime_config import boolean, integer, number
from core import runtime_state
from trade_market_client import collect_multi_exchange_universe, create_trade_market_client
from strategies.analyzers import analyze_strategy, ma55_cycle_event
from strategies.catalog import STRATEGIES, get_strategy
from strategies.fib_pullback import normalize_klines
from strategies.repository import repository

logger = logging.getLogger("strategy_lab")
DEFAULT_STRATEGY = "fib_05_pullback"
_RUN_LOCK = threading.Lock()


def is_strategy_scan_running() -> bool:
    return _RUN_LOCK.locked()


def _iso_ms(ts: float) -> datetime:
    if ts > 10_000_000_000:
        ts /= 1000.0
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _safe_repo(callable_, default=None):
    try:
        return callable_()
    except Exception as exc:
        logger.warning("Strategy repository unavailable: %s", exc)
        return default


def _entry_touched(candle: dict[str, float], direction: str, entry: float, mode: str) -> bool:
    direction = str(direction or "LONG").upper()
    mode = str(mode or "LIMIT").upper()
    if mode == "STOP":
        return candle["high"] >= entry if direction == "LONG" else candle["low"] <= entry
    return candle["low"] <= entry if direction == "LONG" else candle["high"] >= entry


def _bar_resolution(candle: dict[str, float], direction: str, stop: float, tp: float) -> tuple[str, str] | None:
    direction = str(direction or "LONG").upper()
    if direction == "LONG":
        stop_hit = candle["low"] <= stop
        tp_hit = candle["high"] >= tp
    else:
        stop_hit = candle["high"] >= stop
        tp_hit = candle["low"] <= tp
    if stop_hit and tp_hit:
        return ("lost", "SL_AMBIGUOUS")
    if stop_hit:
        return ("lost", "SL")
    if tp_hit:
        return ("won", "TP")
    return None


def _return_pct(direction: str, entry: float, exit_price: float) -> float:
    if entry <= 0:
        return 0.0
    raw = (exit_price / entry - 1.0) * 100.0
    return raw if str(direction).upper() == "LONG" else -raw



def _ma55_cycle_outcome(client, setup: dict[str, Any], now: datetime) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Forward-track MA55 cycle until reverse cross or protective stop."""
    created = datetime.fromisoformat(str(setup["created_at"]).replace("Z", "+00:00"))
    state = setup.get("state") or "waiting_entry"
    direction = "LONG"
    entry = float(setup["entry_price"])
    stop = float(setup["stop_price"])
    entered_at_raw = setup.get("entered_at")
    entered_at = datetime.fromisoformat(str(entered_at_raw).replace("Z", "+00:00")) if entered_at_raw else None
    values: dict[str, Any] = {}

    # Realistic forward fill: first future hourly bar after the strategy was found.
    hourly = normalize_klines(client.klines(setup["symbol"], "1h", 500))
    hourly = hourly[:-1] if len(hourly) > 1 else hourly
    if state == "waiting_entry":
        first = next((x for x in hourly if _iso_ms(x["ts"]) > created), None)
        if first is None:
            if now - created > timedelta(days=3):
                return ({"state": "expired", "outcome": "ENTRY_EXPIRED", "resolved_at": now.isoformat(), "return_pct": 0}, None)
            return ({}, None)
        entry = float(first.get("open") or first.get("close") or entry)
        entered_at = _iso_ms(first["ts"])
        state = "open"
        values.update(state="open", entered_at=entered_at.isoformat(), entry_price=entry)

    if state != "open" or entered_at is None:
        return values, None

    # Protective stop is checked at 1H resolution.
    stop_event = None
    for bar in hourly:
        dt = _iso_ms(bar["ts"])
        if dt < entered_at:
            continue
        if float(bar["low"]) <= stop:
            stop_event = (dt, stop)
            break

    # Normal strategy exit: a completed reverse 55 cross on CLOSED H4 candles.
    h4 = normalize_klines(client.klines(setup["symbol"], "4h", 320))
    h4 = h4[:-1] if len(h4) > 1 else h4
    cross_event = None
    # Walk forward through H4 history and detect the first reverse transition after entry.
    for end in range(60, len(h4) + 1):
        bar_dt = _iso_ms(h4[end - 1]["ts"])
        if bar_dt < entered_at:
            continue
        event = ma55_cycle_event(h4[:end], "EXIT", 12)
        if event:
            cross_event = (bar_dt, float(event.get("price") or h4[end - 1]["close"]))
            break

    candidates = []
    if stop_event:
        candidates.append((stop_event[0], "RISK_SL", stop_event[1]))
    if cross_event:
        candidates.append((cross_event[0], "MA55_REVERSE_CROSS", cross_event[1]))
    if not candidates:
        return values, None

    when, outcome, exit_price = min(candidates, key=lambda x: x[0])
    ret = round(_return_pct(direction, entry, float(exit_price)), 4)
    new_state = "won" if ret > 0 else ("lost" if ret < 0 else "breakeven")
    values.update(state=new_state, outcome=outcome, resolved_at=when.isoformat(), return_pct=ret)
    event = {
        "strategy": "ma55_cycle", "symbol": setup.get("symbol"), "type": "CLOSE",
        "outcome": outcome, "entry_price": entry, "exit_price": float(exit_price),
        "return_pct": ret, "resolved_at": when.isoformat(), "state": new_state,
    }
    return values, event

def update_outcomes(max_rows: int = 5000, strategy: str = DEFAULT_STRATEGY) -> dict[str, Any]:
    spec = get_strategy(strategy)
    active = _safe_repo(lambda: repository.active_setups(spec.key, max_rows), []) or []
    client = create_trade_market_client()
    now = datetime.now(timezone.utc)
    result = {"checked": 0, "opened": 0, "won": 0, "lost": 0, "breakeven": 0, "expired": 0, "errors": 0, "events": []}
    for setup in active:
        result["checked"] += 1
        try:
            if spec.key == "ma55_cycle":
                values, event = _ma55_cycle_outcome(client, setup, now)
                if values:
                    old_state = setup.get("state") or "waiting_entry"
                    new_state = values.get("state", old_state)
                    _safe_repo(lambda s=setup, v=values: repository.update_setup(s["id"], v), None)
                    if old_state == "waiting_entry" and new_state == "open":
                        result["opened"] += 1
                    if new_state in {"won", "lost", "breakeven", "expired"} and new_state != old_state:
                        result[new_state] += 1
                if event:
                    result["events"].append(event)
                continue
            rows = normalize_klines(client.klines(setup["symbol"], "1h", 500))
            # Forward tracking must use completed 1H candles only. The exchange
            # commonly returns the currently forming candle as the last row.
            rows = [x for x in rows if _iso_ms(x["ts"]) + timedelta(hours=1) <= now]
            created = datetime.fromisoformat(str(setup["created_at"]).replace("Z", "+00:00"))
            state = setup.get("state") or "waiting_entry"
            entered_boundary = None
            if setup.get("entered_at"):
                entered_boundary = datetime.fromisoformat(str(setup["entered_at"]).replace("Z", "+00:00"))
            boundary = entered_boundary if state == "open" and entered_boundary else created
            # Keep the boundary candle but never use pre-boundary extrema from it.
            candles = [x for x in rows if _iso_ms(x["ts"]) + timedelta(hours=1) > boundary]
            entry = float(setup["entry_price"])
            stop = float(setup["stop_price"])
            tp = float(setup["tp_price"])
            direction = str(setup.get("direction") or "LONG").upper()
            payload = setup.get("payload") or {}
            entry_mode = str(payload.get("entry_mode") or "LIMIT").upper()
            entered_at = setup.get("entered_at")
            resolved = None
            for candle in candles:
                cdt = _iso_ms(candle["ts"])
                candle_end = cdt + timedelta(hours=1)
                current_boundary = entered_boundary if state == "open" and entered_boundary else created
                partial = cdt < current_boundary < candle_end
                safe_candle = candle
                if partial:
                    close = float(candle.get("close") or 0)
                    safe_candle = {**candle, "open": close, "high": close, "low": close, "close": close}
                if state == "waiting_entry":
                    if _entry_touched(safe_candle, direction, entry, entry_mode):
                        state = "open"
                        # OHLC cannot tell whether TP/SL happened before or after an
                        # intra-bar entry touch. Establish the fill at the end of this
                        # completed bar and start outcome tracking from the NEXT bar.
                        entered_dt = candle_end
                        entered_at = entered_dt.isoformat()
                        entered_boundary = entered_dt
                    continue
                if state == "open":
                    bar = _bar_resolution(safe_candle, direction, stop, tp)
                    if bar:
                        resolved = (bar[0], bar[1], candle_end if partial else cdt)
                        break
            values: dict[str, Any] = {}
            if resolved:
                new_state, outcome, when = resolved
                exit_price = tp if new_state == "won" else stop
                values.update(
                    state=new_state,
                    outcome=outcome,
                    resolved_at=when.isoformat(),
                    return_pct=round(_return_pct(direction, entry, exit_price), 4),
                )
                result[new_state] += 1
            elif state == "open" and setup.get("state") != "open":
                values.update(state="open", entered_at=entered_at)
                result["opened"] += 1
            elif state == "waiting_entry" and now - created > timedelta(days=14):
                values.update(state="expired", outcome="ENTRY_EXPIRED", resolved_at=now.isoformat(), return_pct=0)
                result["expired"] += 1
            if values:
                _safe_repo(lambda s=setup, v=values: repository.update_setup(s["id"], v), None)
        except Exception as exc:
            result["errors"] += 1
            logger.debug("Outcome update failed %s/%s: %s", spec.key, setup.get("symbol"), exc)
    # Keep a durable aggregate in Supabase even when nobody opens the Telegram
    # statistics screen. This survives redeploys and provides daily history.
    try:
        stats(spec.key, persist=True)
    except Exception as exc:
        logger.debug("Strategy statistics refresh failed %s: %s", spec.key, exc)
    return result


def _derivatives_snapshot(client, symbol: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    premium = client.capability("premium_index", symbol)
    if premium.status == "supported":
        out["premium"] = premium.value
        out["premium_provider"] = premium.provider
    oi_hist = client.capability("open_interest_history", symbol, "1h", 24)
    if oi_hist.status == "supported":
        out["oi_history"] = oi_hist.value
        out["oi_provider"] = oi_hist.provider
    out["premium_status"] = premium.status
    out["oi_status"] = oi_hist.status
    return out


def _run_strategy_scan_unlocked(strategy: str, progress=None, force_parallel_budget: bool = False) -> dict[str, Any]:
    spec = get_strategy(strategy)
    common_min = number("STRATEGY_LAB_MIN_VOLUME_USDT", 100_000_000, minimum=1_000_000)
    if spec.key == "fib_05_pullback":
        legacy_min = number("FIB_STRATEGY_MIN_VOLUME_USDT", common_min, minimum=1_000_000)
        min_volume = number(f"STRATEGY_{spec.short.upper()}_MIN_VOLUME_USDT", legacy_min, minimum=1_000_000)
        legacy_max = integer("FIB_STRATEGY_MAX_SYMBOLS", 200, minimum=10, maximum=300)
    else:
        min_volume = number(f"STRATEGY_{spec.short.upper()}_MIN_VOLUME_USDT", common_min, minimum=1_000_000)
        legacy_max = 200
    max_symbols = integer("STRATEGY_LAB_MAX_SYMBOLS", legacy_max, minimum=10, maximum=300)
    main_scanner_running = False
    try:
        from scanner.pipeline import is_trade_scan_running
        main_scanner_running = bool(is_trade_scan_running())
    except Exception:
        main_scanner_running = False
    parallel_mode = bool(force_parallel_budget) or (main_scanner_running and boolean("STRATEGY_LAB_PARALLEL_WITH_MAIN", True))
    if parallel_mode:
        max_symbols = min(
            max_symbols,
            integer("STRATEGY_LAB_PARALLEL_MAX_SYMBOLS", 120, minimum=10, maximum=300),
        )
    parallel_throttle_ms = integer("STRATEGY_LAB_PARALLEL_THROTTLE_MS", 100, minimum=0, maximum=5000) if parallel_mode else 0
    d1_limit = integer("STRATEGY_LAB_D1_LIMIT", 240, minimum=90, maximum=500)
    h4_limit = integer("STRATEGY_LAB_H4_LIMIT", 220, minimum=80, maximum=500)
    state_name = f"strategy_{spec.short}"
    runtime_state.start(state_name, name=spec.key, phase="universe", processed=0, total=0)
    outcome_update = update_outcomes(5000, spec.key)
    universe, providers = collect_multi_exchange_universe(top_limit=300, min_quote_volume=min_volume, timeout=8)
    universe = sorted(universe, key=lambda x: float(x.get("quoteVolume") or 0), reverse=True)
    eligible_total = len(universe)
    universe = universe[:max_symbols]
    runtime_state.update(state_name, phase="analysis", total=len(universe), processed=0)
    client = create_trade_market_client()
    results = []
    errors = []
    new_ready_events = []
    for idx, item in enumerate(universe, 1):
        symbol = item["symbol"]
        try:
            d1 = client.klines(symbol, "1d", d1_limit)
            provider_d1 = client.last_provider
            h4 = client.klines(symbol, "4h", h4_limit)
            derivatives = _derivatives_snapshot(client, symbol) if spec.needs_derivatives else {}
            if spec.needs_h1:
                derivatives["h1_rows"] = client.klines(symbol, "1h", integer("STRATEGY_LAB_H1_LIMIT", 260, minimum=100, maximum=600))
            analysis = analyze_strategy(spec.key, symbol, float(item.get("quoteVolume") or 0), d1, h4, provider_d1 or client.last_provider, derivatives)
            analysis["exchange_count"] = int(item.get("exchangeCount") or 0)
            results.append(analysis)
            if analysis.get("status") == "READY" and analysis.get("entry_price") and analysis.get("stop_price") and analysis.get("tp_price"):
                row = {
                    "strategy": spec.key,
                    "fingerprint": analysis["fingerprint"],
                    "symbol": symbol,
                    "direction": analysis.get("direction") or "LONG",
                    "state": "waiting_entry",
                    "entry_price": analysis["entry_price"],
                    "entry_zone_low": analysis.get("entry_zone_low"),
                    "entry_zone_high": analysis.get("entry_zone_high"),
                    "stop_price": analysis["stop_price"],
                    "tp_price": analysis["tp_price"],
                    "rr": analysis.get("rr"),
                    "score": analysis.get("score"),
                    "d1_low": analysis.get("d1_low"),
                    "d1_high": analysis.get("d1_high"),
                    "fib_05": analysis.get("fib_05"),
                    "support_low": analysis.get("support_low"),
                    "support_high": analysis.get("support_high"),
                    "market_price": analysis.get("market_price"),
                    "payload": analysis,
                    "entered_at": None,
                }
                inserted = bool(_safe_repo(lambda r=row: repository.upsert_setup(r), False))
                if inserted:
                    new_ready_events.append({
                        "strategy": spec.key, "type": "BUY", "symbol": symbol,
                        "direction": row["direction"], "reference_price": analysis.get("market_price") or row["entry_price"],
                        "entry_price": row["entry_price"], "stop_price": row["stop_price"],
                        "tp_price": row["tp_price"], "rr": row.get("rr"),
                        "score": row.get("score"), "reason": analysis.get("reason"),
                        "entry_mode": (analysis.get("entry_mode") or "LIMIT"),
                        "fingerprint": row["fingerprint"],
                    })
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)[:240]})
        runtime_state.update(state_name, processed=idx, parallelWithMain=parallel_mode)
        if progress:
            progress(idx, len(universe), symbol)
        if parallel_throttle_ms:
            time.sleep(parallel_throttle_ms / 1000.0)

    rank = {"READY": 0, "WATCH": 1, "WAITING": 2, "NO_SETUP": 3}
    results.sort(key=lambda x: (rank.get(x.get("status"), 9), -float(x.get("score") or 0), float(x.get("distance_to_zone_pct") or 999)))
    summary = {
        "strategy": spec.key,
        "eligible_total": eligible_total,
        "analyzed": len(universe),
        "ready": sum(1 for x in results if x.get("status") == "READY"),
        "watch": sum(1 for x in results if x.get("status") == "WATCH"),
        "waiting": sum(1 for x in results if x.get("status") == "WAITING"),
        "no_setup": sum(1 for x in results if x.get("status") == "NO_SETUP"),
        "errors": len(errors),
        "min_volume_usdt": min_volume,
        "providers_ok": sum(1 for x in providers.values() if x.get("ok")),
        "run_at": datetime.now(timezone.utc).isoformat(),
        "parallel_with_main": parallel_mode,
        "parallel_budget_symbols": max_symbols if parallel_mode else None,
        "new_ready_events": new_ready_events,
        "outcome_events": list(outcome_update.get("events") or []),
        "funnel": ({
            stage: sum(1 for x in results if x.get("funnel_stage") == stage)
            for stage in sorted({str(x.get("funnel_stage")) for x in results if x.get("funnel_stage")})
        } if spec.key == "ma55_cycle" else {}),
    }
    _safe_repo(lambda: repository.save_run(spec.key, summary, results), None)
    # Include newly-created READY setups in the durable aggregate immediately.
    try:
        stats(spec.key, persist=True)
    except Exception as exc:
        logger.debug("Post-scan statistics refresh failed %s: %s", spec.key, exc)
    runtime_state.finish(state_name, phase="idle", processed=len(universe), total=len(universe), lastSummary=summary)
    return {"strategy": spec.key, "summary": summary, "results": results, "errors": errors}


def run_strategy_scan(strategy: str, progress=None, force_parallel_budget: bool = False) -> dict[str, Any]:
    spec = get_strategy(strategy)
    if not _RUN_LOCK.acquire(blocking=False):
        return {
            "strategy": spec.key,
            "summary": {"strategy": spec.key, "busy": True, "analyzed": 0, "ready": 0, "watch": 0, "waiting": 0, "errors": 0},
            "results": [],
            "errors": [{"error": "Strategy Lab busy"}],
        }
    try:
        return _run_strategy_scan_unlocked(spec.key, progress=progress, force_parallel_budget=force_parallel_budget)
    finally:
        _RUN_LOCK.release()


def run_scan(progress=None):
    """Backward-compatible Fib 0.5 entrypoint used by v25 code/tests."""
    return run_strategy_scan(DEFAULT_STRATEGY, progress=progress)


def latest_run(strategy: str = DEFAULT_STRATEGY):
    spec = get_strategy(strategy)
    return _safe_repo(lambda: repository.latest_run(spec.key), None)


def _compute_stats_from_rows(strategy: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute strategy performance from durable forward setups.

    Rows are expected in chronological order. The calculation deliberately uses
    actual forward outcomes only; WATCH/WAITING candidates never enter PnL.
    """
    resolved = [x for x in rows if x.get("state") in {"won", "lost", "breakeven"}]
    wins = [x for x in resolved if x.get("state") == "won"]
    losses = [x for x in resolved if x.get("state") == "lost"]
    breakeven = [x for x in resolved if x.get("state") == "breakeven"]
    returns = [float(x.get("return_pct") or 0.0) for x in resolved]
    gross_win = sum(max(0.0, x) for x in returns)
    gross_loss = abs(sum(min(0.0, x) for x in returns))

    # Two return views are kept: additive for backward-compatible comparison and
    # compounded for a more realistic equal-capital strategy curve.
    equity = 100.0
    peak = 100.0
    max_drawdown = 0.0
    for value in returns:
        equity *= max(0.0, 1.0 + value / 100.0)
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = min(max_drawdown, (equity / peak - 1.0) * 100.0)

    decisive = len(wins) + len(losses)
    first_at = rows[0].get("created_at") if rows else None
    last_at = rows[-1].get("created_at") if rows else None
    return {
        "strategy": strategy,
        "total": len(rows),
        "resolved": len(resolved),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(breakeven),
        "win_rate": (len(wins) / decisive * 100.0) if decisive else 0.0,
        "avg_return": (sum(returns) / len(returns)) if returns else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0),
        "expectancy": (sum(returns) / len(returns)) if returns else 0.0,
        "cumulative_return": sum(returns),
        "compounded_return": equity - 100.0,
        "max_drawdown": max_drawdown,
        "gross_win": gross_win,
        "gross_loss": gross_loss,
        "waiting": sum(1 for x in rows if x.get("state") == "waiting_entry"),
        "open": sum(1 for x in rows if x.get("state") == "open"),
        "expired": sum(1 for x in rows if x.get("state") == "expired"),
        "first_setup_at": first_at,
        "last_setup_at": last_at,
        "recent": list(reversed(rows[-15:])),
    }


def _persist_strategy_stats(strategy: str, metrics: dict[str, Any]) -> None:
    # `recent` belongs to the UI, not to the aggregate database row.
    durable = {k: v for k, v in metrics.items() if k not in {"recent", "strategy"}}
    _safe_repo(lambda: repository.upsert_statistics(strategy, durable), None)
    _safe_repo(lambda: repository.save_daily_statistics(strategy, durable), None)


def stats(strategy: str = DEFAULT_STRATEGY, persist: bool = True) -> dict[str, Any]:
    spec = get_strategy(strategy)
    rows = _safe_repo(lambda: repository.setups_for_stats(spec.key), None)
    if rows is None:
        # Migration/table/network fallback: keep UI alive using the last persisted
        # aggregate when available. History rows may be absent in this mode.
        cached = _safe_repo(lambda: repository.persisted_statistics(spec.key), None) or {}
        if cached:
            cached = dict(cached)
            cached.setdefault("strategy", spec.key)
            cached.setdefault("recent", [])
            return cached
        rows = []
    metrics = _compute_stats_from_rows(spec.key, rows)
    if persist:
        _persist_strategy_stats(spec.key, metrics)
    return metrics


def leaderboard() -> list[dict[str, Any]]:
    rows = []
    for spec in STRATEGIES:
        s = stats(spec.key)
        rows.append({"key": spec.key, "short": spec.short, "title": spec.title, "emoji": spec.emoji, **s})
    rows.sort(key=lambda x: (x.get("resolved", 0) >= 20, x.get("profit_factor", 0), x.get("expectancy", 0)), reverse=True)
    return rows
