"""AI Optimizer v17.

Analyzes durable Paper Trading history and produces conservative strategy
recommendations. It never loosens filters from closed-trade data alone because
rejected candidates have no realized outcome in this table. Recommendations are
stored in Supabase and require explicit Telegram approval by default.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from core.logging_setup import get_logger
from core.runtime_config import boolean, integer, number
from core.events import emit
from repositories.paper_repository import load_valid_closed_positions
from strategy_settings import current_value, save_setting

log = get_logger("ai_optimizer")


def _client():
    from cloud_client import get_supabase_client
    return get_supabase_client()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _float(name: str, default: float) -> float:
    return number(name, default)


def _int(name: str, default: int) -> int:
    return integer(name, default)


def _bool(name: str, default: bool = True) -> bool:
    return boolean(name, default)

def _rows(limit: int = 1000) -> List[Dict[str, Any]]:
    try:
        return load_valid_closed_positions(limit, ascending=False)
    except Exception:
        log.exception("Optimizer could not load valid filled paper positions")
        return []

def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _metric(rows: Iterable[Dict[str, Any]]) -> Dict[str, float]:
    items = list(rows)
    if not items:
        return {"trades": 0, "wins": 0, "win_rate": 0.0, "pnl": 0.0, "profit_factor": 0.0, "avg_pnl": 0.0}
    pnls = [_num(r.get("net_pnl")) for r in items]
    eps = 1e-9
    wins = sum(1 for x in pnls if x > eps)
    losses = sum(1 for x in pnls if x < -eps)
    breakeven = len(pnls) - wins - losses
    gross_win = sum(x for x in pnls if x > eps)
    gross_loss = abs(sum(x for x in pnls if x < -eps))
    return {
        "trades": len(items),
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": wins / (wins + losses) * 100.0 if (wins + losses) else 0.0,
        "pnl": sum(pnls),
        "profit_factor": gross_win / gross_loss if gross_loss > 1e-12 else (999.0 if gross_win > 0 else 0.0),
        "avg_pnl": sum(pnls) / len(items),
    }


def _candidate_thresholds(key: str, current: float) -> List[float]:
    if key in {"TRADE_MIN_PROBABILITY", "HEDGE_MIN_QUALITY"}:
        steps = [0, 1, 2, 3, 5, 7]
    elif key == "HEDGE_MIN_EV_PCT":
        steps = [0, 0.25, 0.5, 1.0, 1.5, 2.0]
    elif key in {"TRADE_MIN_RR", "QUALITY_MIN_RR"}:
        steps = [0, 0.1, 0.2, 0.3, 0.5, 0.75]
    else:
        steps = [0]
    return sorted({round(current + s, 4) for s in steps})


def _field_value(row: Dict[str, Any], key: str) -> float:
    payload = row.get("signal_payload") or {}
    if key == "HEDGE_MIN_QUALITY":
        return _num(row.get("quality_score") or payload.get("qualityScore"))
    if key == "TRADE_MIN_PROBABILITY":
        return _num(row.get("probability") or payload.get("calibratedProbability") or payload.get("probability"))
    if key == "HEDGE_MIN_EV_PCT":
        return _num(row.get("expected_value_pct") or payload.get("expectedValuePct"))
    if key in {"TRADE_MIN_RR", "QUALITY_MIN_RR"}:
        return _num(payload.get("rr"))
    return 0.0


def _score_option(metrics: Dict[str, float], baseline: Dict[str, float], retention: float) -> float:
    # Conservative objective: reward net PnL and PF, penalize losing too many samples.
    pnl_gain = metrics["pnl"] - baseline["pnl"]
    pf_gain = min(metrics["profit_factor"], 8.0) - min(baseline["profit_factor"], 8.0)
    wr_gain = metrics["win_rate"] - baseline["win_rate"]
    retention_penalty = max(0.0, 0.85 - retention) * 5.0
    return pnl_gain * 3.0 + pf_gain * 0.8 + wr_gain * 0.04 - retention_penalty


def _recommend_filter(rows: List[Dict[str, Any]], key: str) -> Optional[Dict[str, Any]]:
    current = _num(current_value(key))
    baseline = _metric(rows)
    min_retention = _float("AI_OPTIMIZER_MIN_RETENTION", 0.70)
    best = None
    for threshold in _candidate_thresholds(key, current):
        selected = [r for r in rows if _field_value(r, key) >= threshold]
        if not selected:
            continue
        retention = len(selected) / max(1, len(rows))
        if retention < min_retention:
            continue
        metrics = _metric(selected)
        score = _score_option(metrics, baseline, retention)
        item = {"threshold": threshold, "metrics": metrics, "retention": retention, "objective": score}
        if best is None or score > best["objective"]:
            best = item
    if not best or best["threshold"] <= current + 1e-9:
        return None
    min_pnl_gain = _float("AI_OPTIMIZER_MIN_PNL_GAIN_USD", 0.25)
    min_wr_gain = _float("AI_OPTIMIZER_MIN_WIN_RATE_GAIN", 2.0)
    pnl_gain = best["metrics"]["pnl"] - baseline["pnl"]
    wr_gain = best["metrics"]["win_rate"] - baseline["win_rate"]
    if pnl_gain < min_pnl_gain and wr_gain < min_wr_gain:
        return None
    return {
        "kind": "setting",
        "setting_key": key,
        "current_value": current,
        "proposed_value": best["threshold"],
        "baseline": baseline,
        "candidate": best["metrics"],
        "retention_pct": round(best["retention"] * 100, 2),
        "estimated_pnl_delta": round(pnl_gain, 6),
        "estimated_win_rate_delta": round(wr_gain, 3),
        "reason": "Более строгий порог улучшил исторический Paper результат при сохранении достаточного количества сделок.",
    }


def _coin_recommendations(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("symbol") or "UNKNOWN").upper(), []).append(row)
    min_samples = _int("AI_OPTIMIZER_SYMBOL_MIN_SAMPLES", 8)
    result = []
    for symbol, items in grouped.items():
        if len(items) < min_samples:
            continue
        m = _metric(items)
        if m["profit_factor"] < 0.85 and m["pnl"] < 0:
            result.append({
                "kind": "symbol_priority",
                "symbol": symbol,
                "action": "deprioritize",
                "metrics": m,
                "reason": "Устойчивая отрицательная Paper статистика по символу.",
            })
    return sorted(result, key=lambda x: x["metrics"]["pnl"])[:5]


def _universe_recommendation() -> Optional[Dict[str, Any]]:
    try:
        history = (
            _client().table("scanner_scan_history")
            .select("rows_analyzed,signals_count,created_at")
            .order("created_at", desc=True).limit(40).execute().data or []
        )
    except Exception:
        return None
    if len(history) < 8:
        return None
    avg_signals = sum(_num(x.get("signals_count")) for x in history) / len(history)
    avg_rows = sum(_num(x.get("rows_analyzed")) for x in history) / len(history)
    current = _int("TRADE_TOP_LIQUID_SYMBOLS", 30)
    if avg_rows >= current * 0.8 and avg_signals < _float("AI_OPTIMIZER_LOW_SIGNAL_RATE", 0.25):
        ceiling = max(current, _int("AI_OPTIMIZER_UNIVERSE_MAX", 300))
        proposed = min(ceiling, current + max(5, int(round(current * 0.2))))
        if proposed <= current:
            return None
        return {
            "kind": "setting",
            "setting_key": "TRADE_TOP_LIQUID_SYMBOLS",
            "current_value": current,
            "proposed_value": proposed,
            "baseline": {"avg_rows": round(avg_rows, 2), "avg_signals": round(avg_signals, 3), "scans": len(history)},
            "candidate": {},
            "retention_pct": 100.0,
            "estimated_pnl_delta": None,
            "estimated_win_rate_delta": None,
            "reason": "Сканер стабильно заполняет текущий universe, но сигналов мало. Расширение universe безопаснее ослабления Quality/Probability/EV.",
        }
    return None


def run_optimizer(trigger: str = "scheduled") -> Dict[str, Any]:
    emit("OPTIMIZER_STARTED", trigger=trigger)
    rows = _rows(_int("AI_OPTIMIZER_MAX_TRADES", 1000))
    min_samples = _int("AI_OPTIMIZER_MIN_TRADES", 20)
    now = _now()
    run_row: Dict[str, Any] = {
        "status": "completed" if len(rows) >= min_samples else "insufficient_data",
        "trigger": trigger,
        "samples": len(rows),
        "baseline": _metric(rows),
        "created_at": now,
    }
    recommendations: List[Dict[str, Any]] = []
    if len(rows) >= min_samples:
        for key in ("TRADE_MIN_PROBABILITY", "HEDGE_MIN_QUALITY", "HEDGE_MIN_EV_PCT", "TRADE_MIN_RR"):
            rec = _recommend_filter(rows, key)
            if rec:
                recommendations.append(rec)
        recommendations.extend(_coin_recommendations(rows))
    universe = _universe_recommendation()
    if universe:
        recommendations.append(universe)
    run_row["recommendations_count"] = len(recommendations)

    run_id = None
    try:
        resp = _client().table("ai_optimizer_runs").insert(run_row).execute()
        if resp.data:
            run_id = resp.data[0].get("id")
        for rec in recommendations:
            # Do not accumulate identical pending recommendations on every nightly run.
            kind = rec.get("kind")
            key = rec.get("setting_key")
            symbol = rec.get("symbol")
            proposed = None if rec.get("proposed_value") is None else str(rec.get("proposed_value"))
            q = _client().table("ai_optimizer_recommendations").select("id").eq("status", "pending").eq("kind", kind)
            if key:
                q = q.eq("setting_key", key)
            elif symbol:
                q = q.eq("symbol", symbol)
            if proposed is not None:
                q = q.eq("proposed_value", proposed)
            exists = q.limit(1).execute().data or []
            if exists:
                continue
            payload = {
                "run_id": run_id,
                "kind": kind,
                "setting_key": key,
                "symbol": symbol,
                "current_value": None if rec.get("current_value") is None else str(rec.get("current_value")),
                "proposed_value": proposed,
                "reason": rec.get("reason"),
                "metrics": rec,
                "status": "pending",
                "created_at": now,
            }
            _client().table("ai_optimizer_recommendations").insert(payload).execute()
    except Exception:
        log.exception("Failed to persist AI Optimizer result")
    return {"run_id": run_id, **run_row, "recommendations": recommendations}


def get_latest_recommendations(limit: int = 10) -> List[Dict[str, Any]]:
    try:
        response = (
            _client().table("ai_optimizer_recommendations").select("*")
            .eq("status", "pending").order("created_at", desc=True).limit(limit).execute()
        )
        return response.data or []
    except Exception:
        return []


def apply_recommendation(rec_id: str, updated_by: str = "telegram") -> Dict[str, Any]:
    response = _client().table("ai_optimizer_recommendations").select("*").eq("id", rec_id).limit(1).execute()
    if not response.data:
        return {"status": "not_found"}
    rec = response.data[0]
    if rec.get("status") != "pending":
        return {"status": "already_processed"}
    if rec.get("kind") != "setting" or not rec.get("setting_key"):
        return {"status": "manual_only", "recommendation": rec}
    value = save_setting(str(rec["setting_key"]), rec.get("proposed_value"), updated_by=updated_by)
    _client().table("ai_optimizer_recommendations").update({
        "status": "applied", "applied_at": _now(), "applied_by": updated_by
    }).eq("id", rec_id).execute()
    return {"status": "applied", "key": rec["setting_key"], "value": value}


def reject_recommendation(rec_id: str, updated_by: str = "telegram") -> Dict[str, Any]:
    _client().table("ai_optimizer_recommendations").update({
        "status": "rejected", "applied_at": _now(), "applied_by": updated_by
    }).eq("id", rec_id).execute()
    return {"status": "rejected"}
