from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from core.runtime_config import integer, number
from core import runtime_state
from trade_market_client import collect_multi_exchange_universe, create_trade_market_client
from strategies.analyzers import analyze_strategy
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


def update_outcomes(max_rows: int = 80, strategy: str = DEFAULT_STRATEGY) -> dict[str, int]:
    spec = get_strategy(strategy)
    active = _safe_repo(lambda: repository.active_setups(spec.key, max_rows), []) or []
    client = create_trade_market_client()
    now = datetime.now(timezone.utc)
    result = {"checked": 0, "opened": 0, "won": 0, "lost": 0, "expired": 0, "errors": 0}
    for setup in active:
        result["checked"] += 1
        try:
            rows = normalize_klines(client.klines(setup["symbol"], "1h", 500))
            created = datetime.fromisoformat(str(setup["created_at"]).replace("Z", "+00:00"))
            candles = [x for x in rows if _iso_ms(x["ts"]) >= created]
            state = setup.get("state") or "waiting_entry"
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
                if state == "waiting_entry":
                    if _entry_touched(candle, direction, entry, entry_mode):
                        state = "open"
                        entered_at = cdt.isoformat()
                        same_bar = _bar_resolution(candle, direction, stop, tp)
                        if same_bar:
                            resolved = (same_bar[0], same_bar[1], cdt)
                            break
                    continue
                if state == "open":
                    bar = _bar_resolution(candle, direction, stop, tp)
                    if bar:
                        resolved = (bar[0], bar[1], cdt)
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


def _run_strategy_scan_unlocked(strategy: str, progress=None) -> dict[str, Any]:
    spec = get_strategy(strategy)
    common_min = number("STRATEGY_LAB_MIN_VOLUME_USDT", 100_000_000, minimum=1_000_000)
    if spec.key == "fib_05_pullback":
        legacy_min = number("FIB_STRATEGY_MIN_VOLUME_USDT", common_min, minimum=1_000_000)
        min_volume = number(f"STRATEGY_{spec.short.upper()}_MIN_VOLUME_USDT", legacy_min, minimum=1_000_000)
        legacy_max = integer("FIB_STRATEGY_MAX_SYMBOLS", 120, minimum=10, maximum=300)
    else:
        min_volume = number(f"STRATEGY_{spec.short.upper()}_MIN_VOLUME_USDT", common_min, minimum=1_000_000)
        legacy_max = 120
    max_symbols = integer("STRATEGY_LAB_MAX_SYMBOLS", legacy_max, minimum=10, maximum=300)
    d1_limit = integer("STRATEGY_LAB_D1_LIMIT", 240, minimum=90, maximum=500)
    h4_limit = integer("STRATEGY_LAB_H4_LIMIT", 220, minimum=80, maximum=500)
    state_name = f"strategy_{spec.short}"
    runtime_state.start(state_name, name=spec.key, phase="universe", processed=0, total=0)
    update_outcomes(30, spec.key)
    universe, providers = collect_multi_exchange_universe(top_limit=300, min_quote_volume=min_volume, timeout=8)
    universe = sorted(universe, key=lambda x: float(x.get("quoteVolume") or 0), reverse=True)
    eligible_total = len(universe)
    universe = universe[:max_symbols]
    runtime_state.update(state_name, phase="analysis", total=len(universe), processed=0)
    client = create_trade_market_client()
    results = []
    errors = []
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
                _safe_repo(lambda r=row: repository.upsert_setup(r), None)
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)[:240]})
        runtime_state.update(state_name, processed=idx)
        if progress:
            progress(idx, len(universe), symbol)

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
    }
    _safe_repo(lambda: repository.save_run(spec.key, summary, results[:50]), None)
    runtime_state.finish(state_name, phase="idle", processed=len(universe), total=len(universe), lastSummary=summary)
    return {"strategy": spec.key, "summary": summary, "results": results, "errors": errors}


def run_strategy_scan(strategy: str, progress=None) -> dict[str, Any]:
    spec = get_strategy(strategy)
    if not _RUN_LOCK.acquire(blocking=False):
        return {
            "strategy": spec.key,
            "summary": {"strategy": spec.key, "busy": True, "analyzed": 0, "ready": 0, "watch": 0, "waiting": 0, "errors": 0},
            "results": [],
            "errors": [{"error": "Strategy Lab busy"}],
        }
    try:
        return _run_strategy_scan_unlocked(spec.key, progress=progress)
    finally:
        _RUN_LOCK.release()


def run_scan(progress=None):
    """Backward-compatible Fib 0.5 entrypoint used by v25 code/tests."""
    return run_strategy_scan(DEFAULT_STRATEGY, progress=progress)


def latest_run(strategy: str = DEFAULT_STRATEGY):
    spec = get_strategy(strategy)
    return _safe_repo(lambda: repository.latest_run(spec.key), None)


def stats(strategy: str = DEFAULT_STRATEGY) -> dict[str, Any]:
    spec = get_strategy(strategy)
    rows = _safe_repo(lambda: repository.recent_setups(spec.key, 2000), []) or []
    resolved = [x for x in rows if x.get("state") in {"won", "lost"}]
    wins = [x for x in resolved if x.get("state") == "won"]
    losses = [x for x in resolved if x.get("state") == "lost"]
    returns = [float(x.get("return_pct") or 0) for x in resolved]
    gross_win = sum(max(0.0, x) for x in returns)
    gross_loss = abs(sum(min(0.0, x) for x in returns))
    return {
        "strategy": spec.key,
        "total": len(rows),
        "resolved": len(resolved),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(resolved) * 100) if resolved else 0.0,
        "avg_return": (sum(returns) / len(returns)) if returns else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0),
        "expectancy": (sum(returns) / len(returns)) if returns else 0.0,
        "waiting": sum(1 for x in rows if x.get("state") == "waiting_entry"),
        "open": sum(1 for x in rows if x.get("state") == "open"),
        "expired": sum(1 for x in rows if x.get("state") == "expired"),
        "recent": rows[:15],
    }


def leaderboard() -> list[dict[str, Any]]:
    rows = []
    for spec in STRATEGIES:
        s = stats(spec.key)
        rows.append({"key": spec.key, "short": spec.short, "title": spec.title, "emoji": spec.emoji, **s})
    rows.sort(key=lambda x: (x.get("resolved", 0) >= 20, x.get("profit_factor", 0), x.get("expectancy", 0)), reverse=True)
    return rows
