"""Unified AI Score engine for Crypto Report Service v12.

The score is deterministic and explainable. It combines normalized technical,
derivatives, flow and intelligence factors into a 0..100 value. Optional adaptive
weights are read from the existing feature_stats table.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
from core.sqlite_utils import connect as safe_sqlite_connect
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable

from v8_store import connect as v8_connect, initialize as initialize_v8, latest
from learning_engine_v14 import (
    active_model, apply_learning_adjustments, calibrated_probability, classify_regime, specialist_weights
)

DB_PATH = Path(os.getenv("AI_SCORE_DB_PATH", "data/ai_intelligence.db"))
DEFAULT_WEIGHTS = {
    "trend": 1.15,
    "momentum": 1.00,
    "volume": 0.90,
    "funding": 0.70,
    "open_interest": 1.00,
    "alignment": 1.20,
    "risk_reward": 1.00,
    "capital_flow": 0.95,
    "narrative": 0.60,
    "news": 0.55,
    "smart_money": 0.75,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp(value: Any, low: float = 0.0, high: float = 100.0) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return low


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = safe_sqlite_connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_ai_store() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ai_scores(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT,
                symbol TEXT NOT NULL,
                direction TEXT,
                ai_score REAL NOT NULL,
                tier TEXT NOT NULL,
                factors_json TEXT NOT NULL,
                weights_json TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ai_scores_symbol_time
                ON ai_scores(symbol, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_ai_scores_score_time
                ON ai_scores(ai_score DESC, created_at DESC);
            CREATE TABLE IF NOT EXISTS ai_alerts(
                fingerprint TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                ai_score REAL NOT NULL,
                sent_at TEXT NOT NULL
            );
            """
        )


def get_model_config() -> Dict[str, Any]:
    """Return the active validated model, falling back to v12/base weights."""
    try:
        model = active_model(DEFAULT_WEIGHTS)
        if model.get("version") != "12.0-base":
            try:
                from adaptive_cloud_learning import load_cloud_overlay
                from learning_engine_v14 import _apply_operator_weight_policy
                learned = dict(model.get("learned_weights") or model.get("weights") or DEFAULT_WEIGHTS)
                cloud_weights = load_cloud_overlay(learned)
                effective = _apply_operator_weight_policy(cloud_weights, DEFAULT_WEIGHTS)
                model = dict(model)
                model["weights"] = effective
                cfg = dict(model.get("config") or {})
                cfg["global_weights"] = effective
                cfg["cloud_overlay_weights"] = cloud_weights
                model["config"] = cfg
                model["adaptiveCloud"] = cloud_weights != learned
            except Exception:
                pass
            return model
    except Exception:
        model = {"version": "12.0-base", "weights": dict(DEFAULT_WEIGHTS), "rules": [], "metrics": {}}

    # Backward-compatible import of the conservative v12 feature_stats weights.
    weights = dict(DEFAULT_WEIGHTS)
    try:
        initialize_v8()
        with v8_connect() as conn:
            rows = conn.execute("SELECT feature, weight FROM feature_stats").fetchall()
        aliases = {"oi": "open_interest", "risk": "risk_reward"}
        for row in rows:
            key = aliases.get(str(row["feature"]), str(row["feature"]))
            if key in weights and row["weight"] is not None:
                weights[key] = max(0.2, min(2.5, float(row["weight"])))
    except Exception:
        pass
    try:
        from adaptive_cloud_learning import load_cloud_overlay
        weights = load_cloud_overlay(weights)
        model["adaptiveCloud"] = True
    except Exception:
        pass
    model["weights"] = weights
    return model


def get_weights() -> Dict[str, float]:
    return dict(get_model_config().get("weights") or DEFAULT_WEIGHTS)


def _symbol_tokens(symbol: str) -> Iterable[str]:
    base = str(symbol or "").upper().replace("USDT", "").replace("USD", "")
    return {base, symbol.upper()}


def _snapshot_match_score(kind: str, symbol: str, default: float = 50.0) -> float:
    tokens = _symbol_tokens(symbol)
    try:
        rows = latest(kind, 100)
    except Exception:
        return default
    candidates = []
    for row in rows:
        payload = row.get("payload") or {}
        haystack = json.dumps(payload, ensure_ascii=False).upper()
        row_symbol = str(row.get("symbol") or "").upper()
        if row_symbol in tokens or any(token and token in haystack for token in tokens):
            candidates.append(float(row.get("score") or payload.get("score") or payload.get("impact") or 50))
    return _clamp(max(candidates) if candidates else default)


def extract_factors(signal: Dict[str, Any]) -> Dict[str, float]:
    profile = signal.get("tradeProfile") or {}
    rr = _clamp(float(signal.get("rr") or 0) * 22.5)
    risk_profile = _clamp(profile.get("risk", 50))
    factors = {
        "trend": _clamp(profile.get("trend", signal.get("alignment", 50))),
        "momentum": _clamp(profile.get("momentum", signal.get("score", 50))),
        "volume": _clamp(profile.get("volume", 50)),
        "funding": _clamp(profile.get("funding", 50)),
        "open_interest": _clamp(profile.get("oi", 50)),
        "alignment": _clamp(signal.get("alignment", profile.get("trend", 50))),
        "risk_reward": _clamp(0.65 * rr + 0.35 * risk_profile),
        "capital_flow": _snapshot_match_score("capital_flow", signal.get("symbol", "")),
        "narrative": _snapshot_match_score("narrative", signal.get("symbol", "")),
        "news": _snapshot_match_score("news", signal.get("symbol", "")),
        "smart_money": _snapshot_match_score("smart_money", signal.get("symbol", "")),
    }
    # Confidence acts as a conservative multiplier rather than an extra factor.
    confidence = _clamp(signal.get("confidence", profile.get("confidence", 65)))
    factors["confidence_multiplier"] = 0.82 + confidence / 555.0
    return factors


def tier_for(score: float) -> str:
    if score >= 90:
        return "ELITE"
    if score >= 82:
        return "STRONG"
    if score >= 72:
        return "GOOD"
    if score >= 60:
        return "WATCH"
    return "WEAK"


def calculate_ai_score(signal: Dict[str, Any], weights: Dict[str, float] | None = None) -> Dict[str, Any]:
    model = get_model_config()
    factors = extract_factors(signal)
    regime = classify_regime(factors)
    selected_weights = weights or specialist_weights(model, regime, str(signal.get("direction") or ""))
    selected_weights = dict(selected_weights or DEFAULT_WEIGHTS)
    numerator = sum(factors[name] * selected_weights.get(name, DEFAULT_WEIGHTS[name]) for name in DEFAULT_WEIGHTS)
    denominator = sum(selected_weights.get(name, DEFAULT_WEIGHTS[name]) for name in DEFAULT_WEIGHTS)
    raw = numerator / denominator if denominator else 0
    pre_adjustment = _clamp(raw * factors["confidence_multiplier"])
    learning = apply_learning_adjustments(
        factors, model.get("rules") or [], regime=regime, direction=str(signal.get("direction") or "")
    )
    score = round(_clamp(pre_adjustment + learning["adjustment"]), 1)
    probability, uncertainty = calibrated_probability(score, regime, model)
    contributions = {
        name: round((factors[name] * selected_weights.get(name, DEFAULT_WEIGHTS[name])) / denominator, 2)
        for name in DEFAULT_WEIGHTS
    }
    ranked = sorted(contributions.items(), key=lambda item: item[1], reverse=True)
    return {
        "aiScore": score,
        "aiProbability": round(probability * 100, 1),
        "aiUncertainty": round(uncertainty * 100, 1),
        "aiRegime": regime,
        "aiTier": tier_for(score),
        "aiFactors": {k: round(v, 1) for k, v in factors.items() if k != "confidence_multiplier"},
        "aiWeights": {k: round(float(selected_weights.get(k, DEFAULT_WEIGHTS[k])), 3) for k in DEFAULT_WEIGHTS},
        "aiContributions": contributions,
        "aiReasons": [name for name, _ in ranked[:4]],
        "aiVersion": model.get("version", "13.0-base"),
        "aiRawScore": round(pre_adjustment, 1),
        "aiLearningAdjustment": learning["adjustment"],
        "aiNoTradePenalty": learning["penalty"],
        "aiNoTradeRules": learning["triggered"],
    }


def enrich_signal(signal: Dict[str, Any]) -> Dict[str, Any]:
    enriched = dict(signal)
    enriched.update(calculate_ai_score(enriched))
    # Learning MAX 2.0 augments, but never replaces, the validated v14 score.
    try:
        from learning_max2 import predict, explain
        lm = predict(enriched.get("aiFactors") or {}, str(enriched.get("direction") or ""))
        xai = explain(enriched.get("aiFactors") or {}, lm)
        enriched.update({
            "learningMax2": lm,
            "marketRegime": lm.get("regime", enriched.get("aiRegime")),
            "probability": lm.get("probability", enriched.get("aiProbability")),
            "confidence": lm.get("confidence"),
            "uncertainty": lm.get("uncertainty", enriched.get("aiUncertainty")),
            "explainability": xai,
            "strongFactors": xai.get("strong_factors", []),
            "weakFactors": xai.get("weak_factors", []),
            "whyEnter": xai.get("why_enter"),
            "whyNotEnter": xai.get("why_not_enter"),
        })
    except Exception as exc:
        # Local deterministic score is the mandatory fallback; GPT/API failure
        # must not stop signal generation.
        enriched["learningMax2Fallback"] = str(exc)[:300]
        enriched.setdefault("probability", enriched.get("aiProbability"))
        enriched.setdefault("uncertainty", enriched.get("aiUncertainty"))
        enriched.setdefault("marketRegime", enriched.get("aiRegime"))
    return enriched


def save_ai_score(signal: Dict[str, Any]) -> int:
    initialize_ai_store()
    enriched = signal if "aiScore" in signal else enrich_signal(signal)
    with _connect() as conn:
        cursor = conn.execute(
            """INSERT INTO ai_scores(
                fingerprint,symbol,direction,ai_score,tier,factors_json,weights_json,payload_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                enriched.get("fingerprint"), enriched.get("symbol"), enriched.get("direction"),
                enriched.get("aiScore", 0), enriched.get("aiTier", "WEAK"),
                json.dumps(enriched.get("aiFactors", {}), ensure_ascii=False),
                json.dumps(enriched.get("aiWeights", {}), ensure_ascii=False),
                json.dumps(enriched, ensure_ascii=False, default=str), _now(),
            ),
        )
        return int(cursor.lastrowid)


def get_top_scores(limit: int = 10, hours: int = 24) -> list[Dict[str, Any]]:
    initialize_ai_store()
    with _connect() as conn:
        rows = conn.execute(
            """SELECT a.* FROM ai_scores a
            JOIN (SELECT symbol, MAX(id) AS max_id FROM ai_scores
                  WHERE created_at >= datetime('now', ?) GROUP BY symbol) x ON a.id=x.max_id
            ORDER BY a.ai_score DESC LIMIT ?""",
            (f"-{max(1, int(hours))} hours", max(1, int(limit))),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(item.pop("payload_json"))
            item["factors"] = json.loads(item.pop("factors_json"))
            item["weights"] = json.loads(item.pop("weights_json"))
        except Exception:
            pass
        result.append(item)
    return result


def get_score_history(symbol: str, limit: int = 20) -> list[Dict[str, Any]]:
    initialize_ai_store()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT symbol,ai_score,tier,created_at FROM ai_scores WHERE symbol=? ORDER BY id DESC LIMIT ?",
            (symbol.upper(), max(1, int(limit))),
        ).fetchall()
    return [dict(row) for row in rows]


def claim_ai_alert(signal: Dict[str, Any], cooldown_hours: int = 12) -> bool:
    initialize_ai_store()
    fingerprint = str(signal.get("fingerprint") or f"{signal.get('symbol')}:{signal.get('aiScore')}")
    with _connect() as conn:
        row = conn.execute(
            "SELECT sent_at FROM ai_alerts WHERE fingerprint=? AND sent_at >= datetime('now', ?)",
            (fingerprint, f"-{max(1, int(cooldown_hours))} hours"),
        ).fetchone()
        if row:
            return False
        conn.execute(
            "INSERT OR REPLACE INTO ai_alerts(fingerprint,symbol,ai_score,sent_at) VALUES(?,?,?,?)",
            (fingerprint, signal.get("symbol", ""), signal.get("aiScore", 0), _now()),
        )
    return True
