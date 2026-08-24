"""Learning MAX 2.0 compatibility layer.

Extends the existing v14 learner without replacing it. The module is stdlib-only,
keeps online observations in SQLite, exposes specialist ensemble predictions,
feature importance/selection, uncertainty and drift diagnostics.
"""
from __future__ import annotations

import json
import math
import sqlite3
from core.sqlite_utils import connect as safe_sqlite_connect
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Dict, Iterable, Mapping

from ai_score_engine import DEFAULT_WEIGHTS
from learning_engine_v14 import (
    active_model,
    calibrated_probability,
    classify_regime,
    diagnostics,
    specialist_weights,
    train,
)

DB_PATH = Path("data/learning_max2.db")
SPECIALISTS = ("trend", "momentum", "breakout", "reversal", "volatility", "risk")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = safe_sqlite_connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def initialize() -> None:
    with connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS feature_store(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint TEXT UNIQUE,
            symbol TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            market_regime TEXT,
            features_json TEXT NOT NULL,
            context_json TEXT NOT NULL,
            prediction_json TEXT NOT NULL,
            real_result_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_feature_store_symbol_time
            ON feature_store(symbol, observed_at DESC);
        CREATE TABLE IF NOT EXISTS online_updates(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """)


def save_observation(signal: Mapping[str, Any]) -> None:
    """Persist all available signal inputs; missing providers remain explicit nulls."""
    initialize()
    features = dict(signal.get("aiFactors") or signal.get("features") or {})
    context = {
        "ohlcv": signal.get("ohlcv") or signal.get("klines"),
        "indicators": signal.get("indicators"),
        "funding": signal.get("funding") or signal.get("fundingAnalysis"),
        "open_interest": signal.get("openInterest") or signal.get("oiAnalysis"),
        "liquidations": signal.get("liquidations"),
        "exchange_flows": signal.get("exchangeFlows"),
        "whale_activity": signal.get("whaleActivity"),
        "etf_flows": signal.get("etfFlows"),
        "stablecoin_flow": signal.get("stablecoinFlow"),
        "news_sentiment": signal.get("newsSentiment"),
        "smart_money_score": signal.get("smartMoneyScore") or features.get("smart_money"),
    }
    prediction = {
        "direction": signal.get("direction"), "score": signal.get("aiScore") or signal.get("score"),
        "probability": signal.get("probability"), "confidence": signal.get("confidence"),
    }
    fingerprint = str(signal.get("fingerprint") or f"{signal.get('symbol','UNKNOWN')}:{_now()}")
    regime = signal.get("marketRegime") or (classify_regime(features) if features else "unknown")
    with connect() as conn:
        conn.execute("""
        INSERT INTO feature_store(fingerprint,symbol,observed_at,market_regime,features_json,context_json,prediction_json,real_result_json)
        VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(fingerprint) DO UPDATE SET
          market_regime=excluded.market_regime, features_json=excluded.features_json,
          context_json=excluded.context_json, prediction_json=excluded.prediction_json
        """, (fingerprint, str(signal.get("symbol") or "UNKNOWN"), _now(), regime,
              json.dumps(features, ensure_ascii=False), json.dumps(context, ensure_ascii=False),
              json.dumps(prediction, ensure_ascii=False), None))


def update_result(fingerprint: str, result: Mapping[str, Any]) -> None:
    initialize()
    payload = json.dumps(dict(result), ensure_ascii=False)
    with connect() as conn:
        conn.execute("UPDATE feature_store SET real_result_json=? WHERE fingerprint=?", (payload, fingerprint))
        conn.execute("INSERT INTO online_updates(fingerprint,result_json,created_at) VALUES(?,?,?)", (fingerprint, payload, _now()))


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(x)))


def specialist_scores(features: Mapping[str, Any]) -> Dict[str, float]:
    f = {k: _clamp(v if v is not None else 50) for k, v in features.items()}
    g = lambda k: f.get(k, 50.0)
    return {
        "trend": _clamp(.65*g("trend") + .20*g("alignment") + .15*g("open_interest")),
        "momentum": _clamp(.60*g("momentum") + .25*g("volume") + .15*g("news")),
        "breakout": _clamp(.35*g("trend") + .30*g("volume") + .20*g("open_interest") + .15*g("smart_money")),
        "reversal": _clamp(.35*(100-g("momentum")) + .25*g("risk_reward") + .20*g("funding") + .20*g("capital_flow")),
        "volatility": _clamp(.35*g("volume") + .25*g("momentum") + .20*(100-g("risk_reward")) + .20*g("news")),
        "risk": _clamp(.45*g("risk_reward") + .25*g("alignment") + .15*g("funding") + .15*g("open_interest")),
    }


def feature_importance(features: Mapping[str, Any]) -> Dict[str, float]:
    model = active_model(DEFAULT_WEIGHTS)
    weights = dict(model.get("weights") or DEFAULT_WEIGHTS)
    raw = {k: abs(float(weights.get(k, 0))) * abs(float(features.get(k, 50)) - 50) for k in weights}
    total = sum(raw.values()) or 1.0
    return {k: round(v / total * 100, 2) for k, v in sorted(raw.items(), key=lambda x: x[1], reverse=True)}


def select_features(features: Mapping[str, Any], min_importance: float = 3.0) -> Dict[str, Any]:
    imp = feature_importance(features)
    selected = {k: features[k] for k, score in imp.items() if score >= min_importance and k in features}
    return selected or dict(features)


def predict(features: Mapping[str, Any], direction: str = "") -> Dict[str, Any]:
    selected = select_features(features)
    scores = specialist_scores(selected)
    values = list(scores.values())
    specialist_ensemble = mean(values) if values else 50.0
    regime = classify_regime(dict(features))

    # The v14 Champion + operator weight policy is the authoritative learned
    # weighting layer.  Learning MAX specialists remain an independent ensemble,
    # but their output is blended with the actual weighted v14 factor score so
    # Telegram weight controls affect the final probability too.
    model = active_model(DEFAULT_WEIGHTS)
    weights = specialist_weights(model, regime, direction) or model.get("weights") or DEFAULT_WEIGHTS
    denom = sum(max(0.01, float(weights.get(k, DEFAULT_WEIGHTS[k]))) for k in DEFAULT_WEIGHTS)
    weighted_score = (
        sum(float(features.get(k, 50)) * float(weights.get(k, DEFAULT_WEIGHTS[k])) for k in DEFAULT_WEIGHTS) / denom
        if denom else 50.0
    )
    ensemble = _clamp(0.55 * specialist_ensemble + 0.45 * weighted_score)

    try:
        # V14 calibration bins were fitted on the V14 weighted score. Never feed
        # the differently-distributed Learning MAX ensemble into those bins.
        probability01, calibration_uncertainty = calibrated_probability(weighted_score, regime, model)
        calibrated_v14_probability = float(probability01) * 100.0
        probability = _clamp(0.55 * specialist_ensemble + 0.45 * calibrated_v14_probability)
    except Exception:
        calibrated_v14_probability = weighted_score
        probability = ensemble
        calibration_uncertainty = 0.25
    disagreement = pstdev(values) if len(values) > 1 else 0.0
    empirical_uncertainty = disagreement * 2.2 + max(0.0, 55.0 - abs(ensemble - 50.0))
    uncertainty = _clamp(max(empirical_uncertainty, float(calibration_uncertainty) * 100.0))
    confidence = _clamp(100.0 - uncertainty)
    return {
        "model": "Learning MAX 2.0", "regime": regime, "direction": direction,
        "specialists": {k: round(v, 2) for k, v in scores.items()},
        "specialist_ensemble": round(specialist_ensemble, 2),
        "weighted_v14_score": round(weighted_score, 2),
        "calibrated_v14_probability": round(_clamp(calibrated_v14_probability), 2),
        "ensemble_score": round(ensemble, 2), "probability": round(_clamp(probability), 2),
        "confidence": round(confidence, 2), "uncertainty": round(uncertainty, 2),
        "selected_features": list(selected), "feature_importance": feature_importance(features),
    }


def explain(features: Mapping[str, Any], prediction: Mapping[str, Any]) -> Dict[str, Any]:
    importance = prediction.get("feature_importance") or feature_importance(features)
    ranked = list(importance)
    strong = [f"{k}: {float(features.get(k, 50)):.0f}/100" for k in ranked[:3]]
    weak = [f"{k}: {float(features.get(k, 50)):.0f}/100" for k in ranked[-3:]]
    p = float(prediction.get("probability", 50)); c = float(prediction.get("confidence", 50))
    enter = p >= 60 and c >= 55
    return {
        "reasons": strong,
        "strong_factors": strong,
        "weak_factors": weak,
        "confidence": c,
        "probability": p,
        "why_enter": "Согласие ансамбля и достаточная калиброванная вероятность." if enter else "Вход не подтверждён порогами модели.",
        "why_not_enter": "Высокая неопределённость или недостаточная вероятность." if not enter else "Критических запретов ансамбль не обнаружил.",
    }


def train_incremental() -> Dict[str, Any]:
    """Safe online/incremental trigger; promotion remains delegated to v14 gates."""
    return train(DEFAULT_WEIGHTS)


def status() -> Dict[str, Any]:
    data = diagnostics(DEFAULT_WEIGHTS)
    initialize()
    with connect() as conn:
        stored = conn.execute("SELECT COUNT(*) FROM feature_store").fetchone()[0]
        completed = conn.execute("SELECT COUNT(*) FROM feature_store WHERE real_result_json IS NOT NULL").fetchone()[0]
    return {"engine": "Learning MAX 2.0", "stored_signals": stored, "completed": completed, **data}
