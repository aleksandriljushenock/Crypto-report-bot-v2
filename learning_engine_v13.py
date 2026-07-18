"""Controlled self-learning from the bot's own completed predictions.

The engine is deliberately transparent: it trains bounded linear factor weights,
validates them on newer samples, stores model versions, calibrates score buckets,
and derives conservative no-trade warnings. It never changes the active model
without enough data and a measurable out-of-sample improvement.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Sequence

DB_PATH = Path(os.getenv("LEARNING_V13_DB_PATH", "data/learning_v13.db"))
FEATURES = (
    "trend", "momentum", "volume", "funding", "open_interest", "alignment",
    "risk_reward", "capital_flow", "narrative", "news", "smart_money",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def initialize() -> None:
    with connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS model_versions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL,
            weights_json TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            sample_count INTEGER NOT NULL,
            train_count INTEGER NOT NULL,
            validation_count INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            activated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS calibration_bins(
            model_version TEXT NOT NULL,
            score_min INTEGER NOT NULL,
            score_max INTEGER NOT NULL,
            samples INTEGER NOT NULL,
            wins INTEGER NOT NULL,
            avg_return REAL NOT NULL,
            observed_win_rate REAL NOT NULL,
            PRIMARY KEY(model_version, score_min, score_max)
        );
        CREATE TABLE IF NOT EXISTS no_trade_rules(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_version TEXT NOT NULL,
            feature TEXT NOT NULL,
            operator TEXT NOT NULL,
            threshold REAL NOT NULL,
            samples INTEGER NOT NULL,
            win_rate REAL NOT NULL,
            avg_return REAL NOT NULL,
            penalty REAL NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS learning_runs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """)


def _json(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) else value
    except Exception:
        return default


def load_samples(horizon: str = "24h") -> List[Dict[str, Any]]:
    """Load chronological completed predictions from the existing tracker DB."""
    from trade_outcome_tracker import get_connection, initialize_trade_outcomes

    initialize_trade_outcomes()
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT t.fingerprint,t.symbol,t.direction,t.ai_score,t.ai_tier,
                   t.created_at,t.payload_json,o.return_percent,o.result_label,o.observed_at
            FROM tracked_signals t
            JOIN trade_outcomes o ON o.fingerprint=t.fingerprint
            WHERE o.horizon=? AND o.return_percent IS NOT NULL
            ORDER BY t.created_at ASC
        """, (horizon,)).fetchall()
    samples = []
    for row in rows:
        payload = _json(row["payload_json"], {}) or {}
        factors = payload.get("aiFactors") or {}
        if not all(k in factors for k in FEATURES):
            continue
        ret = float(row["return_percent"] or 0)
        samples.append({
            "fingerprint": row["fingerprint"], "symbol": row["symbol"],
            "direction": row["direction"], "created_at": row["created_at"],
            "return": ret, "win": 1 if ret > 0 else 0,
            "old_score": float(row["ai_score"] or payload.get("aiScore") or 0),
            "factors": {k: float(factors.get(k, 50)) for k in FEATURES},
        })
    return samples


def _score(factors: Dict[str, float], weights: Dict[str, float]) -> float:
    denom = sum(max(0.01, float(weights.get(k, 0))) for k in FEATURES)
    if not denom:
        return 0.0
    return max(0.0, min(100.0, sum(factors[k] * weights[k] for k in FEATURES) / denom))


def _corr(xs: Sequence[float], ys: Sequence[float]) -> float:
    if len(xs) < 3 or len(xs) != len(ys):
        return 0.0
    mx, my = mean(xs), mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (dx * dy) if dx and dy else 0.0


def learn_weights(samples: Sequence[Dict[str, Any]], defaults: Dict[str, float]) -> Dict[str, float]:
    """Learn slowly moving, bounded weights from return and win correlation."""
    learned: Dict[str, float] = {}
    returns = [max(-25.0, min(25.0, s["return"])) for s in samples]
    wins = [float(s["win"]) for s in samples]
    max_change = float(os.getenv("LEARNING_MAX_WEIGHT_CHANGE", "0.20"))
    for feature in FEATURES:
        values = [s["factors"][feature] for s in samples]
        signal = 0.65 * _corr(values, returns) + 0.35 * _corr(values, wins)
        multiplier = 1.0 + max(-max_change, min(max_change, signal * 0.55))
        base = float(defaults[feature])
        learned[feature] = round(max(base * (1 - max_change), min(base * (1 + max_change), base * multiplier)), 4)
    return learned


def evaluate(samples: Sequence[Dict[str, Any]], weights: Dict[str, float]) -> Dict[str, float]:
    if not samples:
        return {"samples": 0, "brier": 1.0, "rank_corr": 0.0, "top_win_rate": 0.0, "top_avg_return": 0.0}
    scores = [_score(s["factors"], weights) for s in samples]
    wins = [s["win"] for s in samples]
    returns = [s["return"] for s in samples]
    probs = [max(0.01, min(0.99, score / 100.0)) for score in scores]
    brier = mean((p - w) ** 2 for p, w in zip(probs, wins))
    ranked = sorted(zip(scores, samples), key=lambda x: x[0], reverse=True)
    top_n = max(1, int(len(ranked) * 0.30))
    top = [s for _, s in ranked[:top_n]]
    return {
        "samples": len(samples), "brier": round(brier, 6),
        "rank_corr": round(_corr(scores, returns), 5),
        "top_win_rate": round(mean(s["win"] for s in top) * 100, 2),
        "top_avg_return": round(mean(s["return"] for s in top), 4),
        "overall_win_rate": round(mean(wins) * 100, 2),
        "overall_avg_return": round(mean(returns), 4),
    }


def _candidate_better(base: Dict[str, float], candidate: Dict[str, float]) -> bool:
    min_improvement = float(os.getenv("LEARNING_MIN_IMPROVEMENT", "0.015"))
    brier_gain = (base["brier"] - candidate["brier"]) / max(base["brier"], 1e-9)
    return (
        brier_gain >= min_improvement
        and candidate["rank_corr"] >= base["rank_corr"] - 0.01
        and candidate["top_avg_return"] >= base["top_avg_return"]
    )


def derive_no_trade_rules(samples: Sequence[Dict[str, Any]], model_version: str) -> List[Dict[str, Any]]:
    min_rule_samples = int(os.getenv("LEARNING_RULE_MIN_SAMPLES", "12"))
    rules: List[Dict[str, Any]] = []
    for feature in FEATURES:
        vals = sorted(s["factors"][feature] for s in samples)
        if len(vals) < min_rule_samples * 2:
            continue
        low_cut = vals[max(0, int(len(vals) * 0.25) - 1)]
        high_cut = vals[min(len(vals) - 1, int(len(vals) * 0.75))]
        for op, cut, subset in (
            ("<=", low_cut, [s for s in samples if s["factors"][feature] <= low_cut]),
            (">=", high_cut, [s for s in samples if s["factors"][feature] >= high_cut]),
        ):
            if len(subset) < min_rule_samples:
                continue
            wr = mean(s["win"] for s in subset) * 100
            ar = mean(s["return"] for s in subset)
            if wr <= 38 and ar < 0:
                penalty = min(14.0, max(3.0, abs(ar) * 1.5 + (40 - wr) * 0.12))
                rules.append({"feature": feature, "operator": op, "threshold": round(cut, 2),
                              "samples": len(subset), "win_rate": round(wr, 2),
                              "avg_return": round(ar, 4), "penalty": round(penalty, 2)})
    rules.sort(key=lambda r: (r["avg_return"], r["win_rate"]))
    return rules[:8]


def save_calibration(model_version: str, samples: Sequence[Dict[str, Any]], weights: Dict[str, float]) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM calibration_bins WHERE model_version=?", (model_version,))
        for lo in range(0, 100, 10):
            hi = lo + 9
            subset = [s for s in samples if lo <= _score(s["factors"], weights) <= (100 if hi == 99 else hi + 0.999)]
            if not subset:
                continue
            conn.execute("INSERT INTO calibration_bins VALUES(?,?,?,?,?,?,?)", (
                model_version, lo, hi, len(subset), sum(s["win"] for s in subset),
                mean(s["return"] for s in subset), mean(s["win"] for s in subset) * 100,
            ))


def active_model(defaults: Dict[str, float]) -> Dict[str, Any]:
    initialize()
    with connect() as conn:
        row = conn.execute("SELECT * FROM model_versions WHERE status='active' ORDER BY id DESC LIMIT 1").fetchone()
        rules = conn.execute("SELECT * FROM no_trade_rules WHERE active=1 AND model_version=? ORDER BY penalty DESC", (row["version"],)).fetchall() if row else []
    if not row:
        return {"version": "12.0-base", "weights": dict(defaults), "metrics": {}, "rules": []}
    return {"version": row["version"], "weights": _json(row["weights_json"], dict(defaults)),
            "metrics": _json(row["metrics_json"], {}), "rules": [dict(r) for r in rules]}


def apply_no_trade_penalty(factors: Dict[str, float], rules: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    triggered = []
    total = 0.0
    for rule in rules:
        value = float(factors.get(rule["feature"], 50))
        hit = value <= float(rule["threshold"]) if rule["operator"] == "<=" else value >= float(rule["threshold"])
        if hit:
            total += float(rule["penalty"])
            triggered.append({"feature": rule["feature"], "operator": rule["operator"],
                              "threshold": rule["threshold"], "penalty": rule["penalty"]})
    cap = float(os.getenv("LEARNING_MAX_NO_TRADE_PENALTY", "18"))
    return {"penalty": round(min(cap, total), 2), "triggered": triggered}


def train(defaults: Dict[str, float]) -> Dict[str, Any]:
    initialize()
    samples = load_samples(os.getenv("LEARNING_HORIZON", "24h"))
    min_samples = int(os.getenv("LEARNING_MIN_SAMPLES", "40"))
    if len(samples) < min_samples:
        result = {"status": "collecting-data", "samples": len(samples), "required": min_samples,
                  "active": active_model(defaults)["version"]}
        with connect() as conn:
            conn.execute("INSERT INTO learning_runs(status,summary_json,created_at) VALUES(?,?,?)",
                         (result["status"], json.dumps(result), now_iso()))
        return result

    split = max(1, min(len(samples) - 1, int(len(samples) * 0.70)))
    train_set, validation = samples[:split], samples[split:]
    candidate_weights = learn_weights(train_set, defaults)
    current = active_model(defaults)
    base_metrics = evaluate(validation, current["weights"])
    candidate_metrics = evaluate(validation, candidate_weights)
    promoted = _candidate_better(base_metrics, candidate_metrics)
    version = "13." + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    status = "active" if promoted else "challenger"
    metrics = {"baseline": base_metrics, "candidate": candidate_metrics, "promoted": promoted}
    with connect() as conn:
        if promoted:
            conn.execute("UPDATE model_versions SET status='retired' WHERE status='active'")
        conn.execute("""INSERT INTO model_versions(version,status,weights_json,metrics_json,sample_count,
                     train_count,validation_count,created_at,activated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                     (version, status, json.dumps(candidate_weights), json.dumps(metrics), len(samples),
                      len(train_set), len(validation), now_iso(), now_iso() if promoted else None))
        conn.execute("DELETE FROM no_trade_rules WHERE model_version=?", (version,))
        rules = derive_no_trade_rules(train_set, version)
        for r in rules:
            conn.execute("""INSERT INTO no_trade_rules(model_version,feature,operator,threshold,samples,
                         win_rate,avg_return,penalty,active,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                         (version, r["feature"], r["operator"], r["threshold"], r["samples"],
                          r["win_rate"], r["avg_return"], r["penalty"], 1 if promoted else 0, now_iso()))
    save_calibration(version, validation, candidate_weights)
    result = {"status": status, "samples": len(samples), "train": len(train_set),
              "validation": len(validation), "version": version, "promoted": promoted,
              "metrics": metrics, "weights": candidate_weights, "rules": rules,
              "active": version if promoted else current["version"]}
    with connect() as conn:
        conn.execute("INSERT INTO learning_runs(status,summary_json,created_at) VALUES(?,?,?)",
                     (status, json.dumps(result), now_iso()))
    return result


def diagnostics(defaults: Dict[str, float]) -> Dict[str, Any]:
    initialize()
    model = active_model(defaults)
    samples = load_samples(os.getenv("LEARNING_HORIZON", "24h"))
    metrics = evaluate(samples, model["weights"])
    with connect() as conn:
        bins = [dict(r) for r in conn.execute("SELECT * FROM calibration_bins WHERE model_version=? ORDER BY score_min", (model["version"],)).fetchall()]
        challengers = [dict(r) for r in conn.execute("SELECT version,status,sample_count,created_at,metrics_json FROM model_versions ORDER BY id DESC LIMIT 5").fetchall()]
    return {"active": model, "samples": len(samples), "metrics": metrics, "calibration": bins, "versions": challengers}
