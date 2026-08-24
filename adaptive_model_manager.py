"""Adaptive Models v18: lightweight, versioned Paper-trained probability model.

Uses pure Python logistic regression to avoid adding heavy runtime dependencies.
New candidates are evaluated on a chronological holdout and become champion only
when they beat the stored probability baseline by a configurable margin.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from core.logging_setup import get_logger
from core.runtime_config import integer, number
from core.events import emit
from repositories.paper_repository import load_valid_closed_positions

log = get_logger("adaptive_model_manager")
FEATURES = (
    "quality", "probability", "ev", "score", "rr", "coverage",
    "trend", "volume", "momentum", "alignment", "capital_flow", "smart_money",
)


def _client():
    from cloud_client import get_supabase_client
    return get_supabase_client()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _int(name: str, default: int) -> int:
    return integer(name, default)


def _float(name: str, default: float) -> float:
    return number(name, default)

def _num(v: Any, default: float = 0.0) -> float:
    try: return float(v)
    except Exception: return default


def _sigmoid(z: float) -> float:
    if z >= 0:
        e = math.exp(-min(z, 60.0)); return 1.0 / (1.0 + e)
    e = math.exp(min(z, 60.0)); return e / (1.0 + e)


def _extract(row: Dict[str, Any]) -> Tuple[List[float], int]:
    p = row.get("signal_payload") or {}
    factors = p.get("aiFactors") or {}
    venues = p.get("exchangeCoverage") or p.get("venues") or []
    if isinstance(venues, dict):
        coverage = _num(venues.get("count"), 1.0)
    elif isinstance(venues, list):
        coverage = float(len(venues))
    else:
        coverage = _num(p.get("exchangeCoverageCount"), 1.0)
    x = [
        _num(row.get("quality_score") or p.get("qualityScore"), 50),
        _num(row.get("probability") or p.get("calibratedProbability") or p.get("probability"), 50),
        _num(row.get("expected_value_pct") or p.get("expectedValuePct"), 0),
        _num(p.get("aiScore") or p.get("score"), 50),
        _num(p.get("rr"), 1), coverage,
        _num(factors.get("trend"), 50), _num(factors.get("volume"), 50),
        _num(factors.get("momentum"), 50), _num(factors.get("alignment"), 50),
        _num(factors.get("capital_flow"), 50), _num(factors.get("smart_money"), 50),
    ]
    y = 1 if _num(row.get("net_pnl")) > 0 else 0
    return x, y


def _load_rows(limit: int) -> List[Dict[str, Any]]:
    try:
        return load_valid_closed_positions(limit, ascending=True)
    except Exception:
        log.exception("Adaptive model could not load valid filled paper positions")
        return []

def _standardize(xs: List[List[float]]) -> Tuple[List[List[float]], List[float], List[float]]:
    n = len(xs); d = len(FEATURES)
    means = [sum(row[j] for row in xs) / n for j in range(d)]
    stds = []
    for j in range(d):
        var = sum((row[j] - means[j]) ** 2 for row in xs) / max(1, n - 1)
        stds.append(max(math.sqrt(var), 1e-6))
    zx = [[(row[j] - means[j]) / stds[j] for j in range(d)] for row in xs]
    return zx, means, stds


def _train(xs: List[List[float]], ys: List[int]) -> Tuple[List[float], float]:
    d = len(FEATURES); w = [0.0] * d
    prior = min(0.98, max(0.02, sum(ys) / max(1, len(ys))))
    b = math.log(prior / (1.0 - prior))
    lr = _float("ADAPTIVE_MODEL_LEARNING_RATE", 0.04)
    l2 = _float("ADAPTIVE_MODEL_L2", 0.02)
    epochs = _int("ADAPTIVE_MODEL_EPOCHS", 500)
    for _ in range(max(50, min(epochs, 2000))):
        gw = [0.0] * d; gb = 0.0
        for x, y in zip(xs, ys):
            p = _sigmoid(b + sum(a * c for a, c in zip(w, x)))
            err = p - y
            gb += err
            for j in range(d): gw[j] += err * x[j]
        n = max(1, len(xs))
        b -= lr * gb / n
        for j in range(d):
            w[j] -= lr * (gw[j] / n + l2 * w[j])
    return w, b


def _predict(x: List[float], model: Dict[str, Any]) -> float:
    means = model["means"]; stds = model["stds"]; w = model["weights"]; b = model["bias"]
    z = b
    for j, value in enumerate(x):
        z += w[j] * ((value - means[j]) / max(stds[j], 1e-6))
    return _sigmoid(z)


def _metrics(rows: List[Dict[str, Any]], model: Dict[str, Any]) -> Dict[str, float]:
    if not rows: return {"samples": 0, "accuracy": 0, "log_loss": 99, "brier": 99}
    loss = 0.0; brier = 0.0; correct = 0; base_loss = 0.0; base_brier = 0.0
    eps = 1e-6
    for row in rows:
        x, y = _extract(row); p = min(1-eps, max(eps, _predict(x, model)))
        base = min(1-eps, max(eps, _num(row.get("probability"), 50) / 100.0))
        loss += -(y * math.log(p) + (1-y) * math.log(1-p))
        base_loss += -(y * math.log(base) + (1-y) * math.log(1-base))
        brier += (p-y)**2; base_brier += (base-y)**2
        correct += int((p >= .5) == bool(y))
    n = len(rows)
    return {"samples": n, "accuracy": correct/n, "log_loss": loss/n, "brier": brier/n,
            "baseline_log_loss": base_loss/n, "baseline_brier": base_brier/n}


def train_candidate(trigger: str = "scheduled") -> Dict[str, Any]:
    # Runtime Model Control must disable every automatic learner, not only v14.
    # Manual Telegram training remains available even while auto-learning is OFF.
    if str(trigger).lower() == "scheduled":
        try:
            from model_control import auto_learning_enabled
            if not auto_learning_enabled():
                return {"status": "disabled-by-runtime-setting", "trigger": trigger}
        except Exception:
            pass
    emit("MODEL_TRAIN_STARTED", trigger=trigger)
    rows = _load_rows(_int("ADAPTIVE_MODEL_MAX_TRADES", 1500))
    min_samples = _int("ADAPTIVE_MODEL_MIN_TRADES", 40)
    min_validation = _int("ADAPTIVE_MODEL_MIN_VALIDATION", 12)
    if len(rows) < min_samples:
        return {"status": "insufficient_data", "samples": len(rows), "required": min_samples}
    split = max(min_samples - min_validation, int(len(rows) * 0.72))
    split = min(split, len(rows)-min_validation)
    train_rows, val_rows = rows[:split], rows[split:]
    raw_x = [_extract(r)[0] for r in train_rows]; ys = [_extract(r)[1] for r in train_rows]
    zx, means, stds = _standardize(raw_x)
    weights, bias = _train(zx, ys)
    model = {"features": list(FEATURES), "means": means, "stds": stds, "weights": weights, "bias": bias}
    metrics = _metrics(val_rows, model)
    improvement = metrics["baseline_log_loss"] - metrics["log_loss"]
    min_improvement = _float("ADAPTIVE_MODEL_MIN_LOGLOSS_IMPROVEMENT", 0.01)
    promote = metrics["samples"] >= min_validation and improvement >= min_improvement
    version = datetime.now(timezone.utc).strftime("paper-logit-%Y%m%d-%H%M%S")
    row = {
        "version": version, "status": "champion" if promote else "candidate", "algorithm": "pure_python_logistic_v1",
        "samples_train": len(train_rows), "samples_validation": len(val_rows), "metrics": metrics,
        "model_json": model, "trigger": trigger, "created_at": _now(), "activated_at": _now() if promote else None,
    }
    try:
        if promote:
            _client().table("adaptive_model_versions").update({"status": "archived"}).eq("status", "champion").execute()
        _client().table("adaptive_model_versions").insert(row).execute()
    except Exception:
        log.exception("Adaptive model persistence failed")
    emit("MODEL_PROMOTED" if promote else "MODEL_CANDIDATE", version=version, improvement=improvement, samples_validation=len(val_rows))
    return {"status": "promoted" if promote else "candidate", "version": version, "metrics": metrics,
            "samples_train": len(train_rows), "samples_validation": len(val_rows), "improvement": improvement}


def latest_models(limit: int = 5) -> List[Dict[str, Any]]:
    try:
        return (_client().table("adaptive_model_versions").select("version,status,algorithm,samples_train,samples_validation,metrics,created_at,activated_at")
                .order("created_at", desc=True).limit(limit).execute().data or [])
    except Exception:
        return []
