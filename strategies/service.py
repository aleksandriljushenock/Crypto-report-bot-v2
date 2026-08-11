from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from core.runtime_config import integer, number
from core import runtime_state
from trade_market_client import collect_multi_exchange_universe, create_trade_market_client
from strategies.fib_pullback import analyze_symbol, normalize_klines
from strategies.repository import repository

logger = logging.getLogger("fib_strategy")
STRATEGY = "fib_05_pullback"


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


def update_outcomes(max_rows: int = 40) -> dict[str, int]:
    active = _safe_repo(lambda: repository.active_setups(STRATEGY, max_rows), []) or []
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
            entered_at = setup.get("entered_at")
            resolved = None
            for candle in candles:
                cdt = _iso_ms(candle["ts"])
                if state == "waiting_entry":
                    if candle["low"] <= entry:
                        state = "open"
                        entered_at = cdt.isoformat()
                        # Conservative same-candle ordering.
                        if candle["low"] <= stop:
                            resolved = ("lost", "SL", cdt)
                            break
                        if candle["high"] >= tp:
                            resolved = ("won", "TP", cdt)
                            break
                    continue
                if state == "open":
                    if candle["low"] <= stop and candle["high"] >= tp:
                        resolved = ("lost", "SL_AMBIGUOUS", cdt)
                        break
                    if candle["low"] <= stop:
                        resolved = ("lost", "SL", cdt)
                        break
                    if candle["high"] >= tp:
                        resolved = ("won", "TP", cdt)
                        break
            values: dict[str, Any] = {}
            if resolved:
                new_state, outcome, when = resolved
                pnl_pct = ((tp / entry - 1) * 100) if new_state == "won" else ((stop / entry - 1) * 100)
                values.update(state=new_state, outcome=outcome, resolved_at=when.isoformat(), return_pct=round(pnl_pct, 4))
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
            logger.debug("Outcome update failed %s: %s", setup.get("symbol"), exc)
    return result


def run_scan(progress=None) -> dict[str, Any]:
    min_volume = number("FIB_STRATEGY_MIN_VOLUME_USDT", 100_000_000, minimum=1_000_000)
    max_symbols = integer("FIB_STRATEGY_MAX_SYMBOLS", 120, minimum=10, maximum=300)
    d1_limit = integer("FIB_STRATEGY_D1_LIMIT", 180, minimum=90, maximum=500)
    h4_limit = integer("FIB_STRATEGY_H4_LIMIT", 180, minimum=80, maximum=500)
    runtime_state.start("strategy_fib", name="fib_05_pullback", phase="universe", processed=0, total=0)
    update_outcomes(30)
    universe, providers = collect_multi_exchange_universe(top_limit=300, min_quote_volume=min_volume, timeout=8)
    universe = sorted(universe, key=lambda x: float(x.get("quoteVolume") or 0), reverse=True)
    eligible_total = len(universe)
    universe = universe[:max_symbols]
    runtime_state.update("strategy_fib", phase="analysis", total=len(universe), processed=0)
    client = create_trade_market_client()
    results = []
    errors = []
    for idx, item in enumerate(universe, 1):
        symbol = item["symbol"]
        try:
            d1 = client.klines(symbol, "1d", d1_limit)
            provider_d1 = client.last_provider
            h4 = client.klines(symbol, "4h", h4_limit)
            analysis = analyze_symbol(symbol, float(item.get("quoteVolume") or 0), d1, h4, provider_d1 or client.last_provider)
            analysis["exchange_count"] = int(item.get("exchangeCount") or 0)
            results.append(analysis)
            if analysis.get("status") == "READY":
                # Forward statistics start after the scan. We intentionally do not
                # back-fill an entry from the H4 candle that created the signal.
                touched = False
                row = {
                    "strategy": STRATEGY,
                    "fingerprint": analysis["fingerprint"],
                    "symbol": symbol,
                    "direction": "LONG",
                    "state": "open" if touched else "waiting_entry",
                    "entry_price": analysis["entry_price"],
                    "entry_zone_low": analysis["entry_zone_low"],
                    "entry_zone_high": analysis["entry_zone_high"],
                    "stop_price": analysis["stop_price"],
                    "tp_price": analysis["tp_price"],
                    "rr": analysis["rr"],
                    "score": analysis["score"],
                    "d1_low": analysis["d1_low"],
                    "d1_high": analysis["d1_high"],
                    "fib_05": analysis["fib_05"],
                    "support_low": analysis["support_low"],
                    "support_high": analysis["support_high"],
                    "market_price": analysis["market_price"],
                    "payload": analysis,
                    "entered_at": datetime.now(timezone.utc).isoformat() if touched else None,
                }
                _safe_repo(lambda r=row: repository.upsert_setup(r), None)
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)[:240]})
        runtime_state.update("strategy_fib", processed=idx)
        if progress:
            progress(idx, len(universe), symbol)

    rank = {"READY": 0, "WATCH": 1, "WAITING": 2, "NO_SETUP": 3}
    results.sort(key=lambda x: (rank.get(x.get("status"), 9), -float(x.get("score") or 0), float(x.get("distance_to_zone_pct") or 999)))
    summary = {
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
    _safe_repo(lambda: repository.save_run(STRATEGY, summary, results[:40]), None)
    runtime_state.finish("strategy_fib", phase="idle", processed=len(universe), total=len(universe), lastSummary=summary)
    return {"summary": summary, "results": results, "errors": errors}


def latest_run():
    return _safe_repo(lambda: repository.latest_run(STRATEGY), None)


def stats() -> dict[str, Any]:
    rows = _safe_repo(lambda: repository.recent_setups(STRATEGY, 1500), []) or []
    resolved = [x for x in rows if x.get("state") in {"won", "lost"}]
    wins = [x for x in resolved if x.get("state") == "won"]
    losses = [x for x in resolved if x.get("state") == "lost"]
    returns = [float(x.get("return_pct") or 0) for x in resolved]
    gross_win = sum(max(0.0, x) for x in returns)
    gross_loss = abs(sum(min(0.0, x) for x in returns))
    return {
        "total": len(rows),
        "resolved": len(resolved),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(resolved) * 100) if resolved else 0.0,
        "avg_return": (sum(returns) / len(returns)) if returns else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0),
        "waiting": sum(1 for x in rows if x.get("state") == "waiting_entry"),
        "open": sum(1 for x in rows if x.get("state") == "open"),
        "expired": sum(1 for x in rows if x.get("state") == "expired"),
        "recent": rows[:15],
    }
