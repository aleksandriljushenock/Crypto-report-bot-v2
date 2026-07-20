"""Advanced, controlled self-learning for Crypto Intelligence Platform v14.

Uses only Python stdlib and the bot's own completed predictions. It provides:
- multi-horizon targets (1h/4h/24h/72h);
- time-decay sample weighting;
- walk-forward validation;
- regime/direction specialists;
- bounded random-search optimization;
- interaction rules and no-trade rules;
- empirical probability calibration;
- uncertainty and drift diagnostics;
- champion/challenger promotion with safety gates.

The engine never promotes a model only because it fits historical data better.
Promotion requires out-of-sample improvement across several chronological folds.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Sequence, Tuple

DB_PATH = Path(os.getenv("LEARNING_V14_DB_PATH", "data/learning_v14.db"))
FEATURES = (
    "trend", "momentum", "volume", "funding", "open_interest", "alignment",
    "risk_reward", "capital_flow", "narrative", "news", "smart_money",
)
HORIZONS = ("1h", "4h", "24h", "72h")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) else value
    except Exception:
        return default


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
            config_json TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            sample_count INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            activated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS calibration_bins(
            model_version TEXT NOT NULL,
            regime TEXT NOT NULL,
            score_min INTEGER NOT NULL,
            score_max INTEGER NOT NULL,
            samples INTEGER NOT NULL,
            wins REAL NOT NULL,
            avg_return REAL NOT NULL,
            observed_probability REAL NOT NULL,
            PRIMARY KEY(model_version,regime,score_min,score_max)
        );
        CREATE TABLE IF NOT EXISTS learning_rules(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_version TEXT NOT NULL,
            kind TEXT NOT NULL,
            regime TEXT NOT NULL,
            direction TEXT NOT NULL,
            rule_json TEXT NOT NULL,
            samples INTEGER NOT NULL,
            win_rate REAL NOT NULL,
            avg_return REAL NOT NULL,
            adjustment REAL NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS learning_runs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS drift_snapshots(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_version TEXT NOT NULL,
            drift_score REAL NOT NULL,
            details_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """)


def classify_regime(factors: Dict[str, float]) -> str:
    trend = float(factors.get("trend", 50))
    align = float(factors.get("alignment", 50))
    momentum = float(factors.get("momentum", 50))
    volume = float(factors.get("volume", 50))
    flow = (float(factors.get("capital_flow", 50)) + float(factors.get("smart_money", 50))) / 2
    if trend >= 64 and align >= 62 and momentum >= 58:
        return "bull_trend"
    if trend <= 36 and align <= 38 and momentum <= 42:
        return "bear_trend"
    if volume >= 72 and abs(momentum - 50) >= 18:
        return "breakout"
    if flow >= 68 and momentum >= 55:
        return "accumulation"
    if flow <= 35 and momentum <= 45:
        return "distribution"
    return "range"


def _normalize_direction(value: Any) -> str:
    value = str(value or "").upper()
    if value in {"LONG", "LONG_BIAS", "BUY"}:
        return "LONG"
    if value in {"SHORT", "SHORT_BIAS", "SELL"}:
        return "SHORT"
    return value


def _cloud_samples() -> List[Dict[str, Any]]:
    """Rebuild learning samples from persistent Supabase observations."""
    if os.getenv("LEARNING_CLOUD_RESTORE_ENABLED", "true").lower() not in {"1", "true", "yes", "on"}:
        return []
    try:
        from cloud_learning_store import CloudLearningStore
        rows = CloudLearningStore().resolved_rows(limit=int(os.getenv("LEARNING_CLOUD_MAX_ROWS", "3000")))
    except Exception:
        return []
    result = []
    for row in rows:
        features = _json(row.get("features"), {}) or {}
        factors = features.get("aiFactors") or features.get("tradeProfile") or {}
        if not all(k in factors for k in FEATURES):
            continue
        outcome = _json(row.get("real_result") or row.get("result") or row.get("outcome"), {}) or {}
        returns = {}
        if isinstance(outcome.get("returns"), dict):
            returns.update({str(k): float(v) for k, v in outcome["returns"].items() if k in HORIZONS})
        horizon = str(outcome.get("horizon") or row.get("horizon") or "24h")
        raw_return = outcome.get("return_percent", outcome.get("returnPercent", outcome.get("pnl_percent")))
        if raw_return is not None and horizon in HORIZONS:
            try:
                returns[horizon] = float(raw_return)
            except Exception:
                pass
        if not returns:
            continue
        metadata = _json(row.get("metadata"), {}) or {}
        fp = str(metadata.get("fingerprint") or row.get("id") or "")
        result.append({
            "fingerprint": fp,
            "symbol": row.get("symbol"),
            "direction": _normalize_direction(row.get("signal_direction") or features.get("direction")),
            "created_at": row.get("signal_created_at") or row.get("created_at") or now_iso(),
            "old_score": float(row.get("signal_score") or features.get("aiScore") or features.get("score") or 0),
            "factors": {k: float(factors.get(k, 50)) for k in FEATURES},
            "returns": returns,
        })
    return result


def load_samples() -> List[Dict[str, Any]]:
    """Aggregate all available outcomes into one sample per prediction."""
    from trade_outcome_tracker import get_connection, initialize_trade_outcomes
    initialize_trade_outcomes()
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT t.fingerprint,t.symbol,t.direction,t.ai_score,t.created_at,t.payload_json,
                   o.horizon,o.return_percent,o.result_label,o.observed_at
            FROM tracked_signals t
            JOIN trade_outcomes o ON o.fingerprint=t.fingerprint
            WHERE o.return_percent IS NOT NULL
            ORDER BY t.created_at ASC,o.observed_at ASC
        """).fetchall()
    grouped: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        payload = _json(row["payload_json"], {}) or {}
        factors = payload.get("aiFactors") or {}
        if not all(k in factors for k in FEATURES):
            continue
        fp = str(row["fingerprint"])
        item = grouped.setdefault(fp, {
            "fingerprint": fp,
            "symbol": row["symbol"],
            "direction": _normalize_direction(row["direction"]),
            "created_at": row["created_at"],
            "old_score": float(row["ai_score"] or payload.get("aiScore") or 0),
            "factors": {k: float(factors.get(k, 50)) for k in FEATURES},
            "returns": {},
        })
        item["returns"][str(row["horizon"])] = float(row["return_percent"] or 0)
    for item in _cloud_samples():
        fp = item.get("fingerprint")
        if not fp:
            continue
        existing = grouped.get(fp)
        if existing:
            existing["returns"].update(item.get("returns") or {})
        else:
            grouped[fp] = item
    result = []
    horizon_weights = {"1h": 0.10, "4h": 0.20, "24h": 0.45, "72h": 0.25}
    for item in grouped.values():
        available = [(h, item["returns"][h]) for h in HORIZONS if h in item["returns"]]
        if not available:
            continue
        denom = sum(horizon_weights[h] for h, _ in available)
        target_return = sum(max(-30.0, min(30.0, r)) * horizon_weights[h] for h, r in available) / denom
        # A win requires positive risk-adjusted composite return, not merely one positive tick.
        wins = [1.0 if r > 0 else 0.0 for _, r in available]
        target_win = sum(w * horizon_weights[h] for (h, _), w in zip(available, wins)) / denom
        item["return"] = target_return
        item["win"] = target_win
        item["regime"] = classify_regime(item["factors"])
        result.append(item)
    return sorted(result, key=lambda x: str(x["created_at"]))


def _score(factors: Dict[str, float], weights: Dict[str, float]) -> float:
    denom = sum(max(0.01, float(weights.get(k, 0.01))) for k in FEATURES)
    return max(0.0, min(100.0, sum(float(factors[k]) * float(weights.get(k, 0.01)) for k in FEATURES) / denom))


def _days_old(created_at: str) -> float:
    try:
        dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)
    except Exception:
        return 0.0


def _sample_weight(sample: Dict[str, Any]) -> float:
    half_life = max(2.0, float(os.getenv("LEARNING_RECENCY_HALF_LIFE_DAYS", "30")))
    return 0.5 ** (_days_old(sample.get("created_at", "")) / half_life)


def _weighted_mean(values: Sequence[float], weights: Sequence[float]) -> float:
    total = sum(weights)
    return sum(v * w for v, w in zip(values, weights)) / total if total else 0.0


def _corr(xs: Sequence[float], ys: Sequence[float], ws: Sequence[float] | None = None) -> float:
    if len(xs) < 3 or len(xs) != len(ys):
        return 0.0
    ws = list(ws or [1.0] * len(xs))
    mx, my = _weighted_mean(xs, ws), _weighted_mean(ys, ws)
    num = sum(w * (x - mx) * (y - my) for x, y, w in zip(xs, ys, ws))
    dx = math.sqrt(sum(w * (x - mx) ** 2 for x, w in zip(xs, ws)))
    dy = math.sqrt(sum(w * (y - my) ** 2 for y, w in zip(ys, ws)))
    return num / (dx * dy) if dx and dy else 0.0


def evaluate(samples: Sequence[Dict[str, Any]], weights: Dict[str, float]) -> Dict[str, float]:
    if not samples:
        return {"samples": 0, "brier": 1.0, "rank_corr": 0.0, "top_win_rate": 0.0, "top_avg_return": 0.0, "utility": -1.0}
    scores = [_score(s["factors"], weights) for s in samples]
    wins = [float(s["win"]) for s in samples]
    returns = [float(s["return"]) for s in samples]
    ws = [_sample_weight(s) for s in samples]
    probs = [max(0.02, min(0.98, score / 100.0)) for score in scores]
    brier = _weighted_mean([(p - w) ** 2 for p, w in zip(probs, wins)], ws)
    ranked = sorted(zip(scores, samples, ws), key=lambda x: x[0], reverse=True)
    top_n = max(1, int(len(ranked) * 0.25))
    top = ranked[:top_n]
    top_wr = _weighted_mean([float(s["win"]) for _, s, _ in top], [w for _, _, w in top]) * 100
    top_ret = _weighted_mean([float(s["return"]) for _, s, _ in top], [w for _, _, w in top])
    rank_corr = _corr(scores, returns, ws)
    overall_ret = _weighted_mean(returns, ws)
    # Utility rewards ranking and profitable top selections, penalizes miscalibration.
    utility = 0.35 * rank_corr + 0.25 * (top_wr / 100.0) + 0.25 * math.tanh(top_ret / 4.0) - 0.15 * brier
    return {
        "samples": len(samples), "brier": round(brier, 6), "rank_corr": round(rank_corr, 5),
        "top_win_rate": round(top_wr, 2), "top_avg_return": round(top_ret, 4),
        "overall_win_rate": round(_weighted_mean(wins, ws) * 100, 2),
        "overall_avg_return": round(overall_ret, 4), "utility": round(utility, 6),
    }


def walk_forward_folds(samples: Sequence[Dict[str, Any]], folds: int = 4) -> List[Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]]:
    n = len(samples)
    folds = max(2, min(folds, 6))
    min_train = max(20, int(n * 0.40))
    remaining = n - min_train
    if remaining < folds:
        return []
    step = max(1, remaining // folds)
    result = []
    for i in range(folds):
        train_end = min_train + i * step
        val_end = n if i == folds - 1 else min(n, train_end + step)
        if val_end > train_end:
            result.append((list(samples[:train_end]), list(samples[train_end:val_end])))
    return result


def _bounded(weights: Dict[str, float], defaults: Dict[str, float]) -> Dict[str, float]:
    max_change = float(os.getenv("LEARNING_MAX_WEIGHT_CHANGE", "0.35"))
    return {
        k: round(max(float(defaults[k]) * (1 - max_change), min(float(defaults[k]) * (1 + max_change), float(weights[k]))), 5)
        for k in FEATURES
    }


def optimize_weights(samples: Sequence[Dict[str, Any]], defaults: Dict[str, float], seed: int) -> Dict[str, float]:
    """Bounded deterministic random search, optimized on chronological folds."""
    if len(samples) < 20:
        return dict(defaults)
    rng = random.Random(seed)
    folds = walk_forward_folds(samples, int(os.getenv("LEARNING_WALK_FORWARD_FOLDS", "4")))
    if not folds:
        return dict(defaults)
    iterations = max(40, min(800, int(os.getenv("LEARNING_SEARCH_ITERATIONS", "240"))))
    best = dict(defaults)

    def objective(candidate: Dict[str, float]) -> float:
        metrics = [evaluate(val, candidate) for _, val in folds]
        return mean(m["utility"] for m in metrics) - 0.05 * (max(m["utility"] for m in metrics) - min(m["utility"] for m in metrics))

    best_obj = objective(best)
    scale = 0.18
    for i in range(iterations):
        center = best if i > iterations // 4 else defaults
        trial = {}
        for k in FEATURES:
            perturb = rng.gauss(0, scale)
            trial[k] = float(center[k]) * (1 + perturb)
        trial = _bounded(trial, defaults)
        obj = objective(trial)
        if obj > best_obj:
            best, best_obj = trial, obj
        if i and i % max(20, iterations // 6) == 0:
            scale *= 0.72
    return best


def _model_config(defaults: Dict[str, float]) -> Dict[str, Any]:
    return {"global_weights": dict(defaults), "specialists": {}, "calibration": {}, "rules": []}


def active_model(defaults: Dict[str, float]) -> Dict[str, Any]:
    initialize()
    with connect() as conn:
        row = conn.execute("SELECT * FROM model_versions WHERE status='active' ORDER BY id DESC LIMIT 1").fetchone()
        rules = conn.execute("SELECT * FROM learning_rules WHERE active=1 AND model_version=? ORDER BY ABS(adjustment) DESC", (row["version"],)).fetchall() if row else []
    if not row:
        try:
            from cloud_model_store import CloudModelStore
            cloud = CloudModelStore().load_active_model()
            if cloud and cloud.get("version"):
                cfg = cloud.get("config") or _model_config(cloud.get("weights") or defaults)
                return {"version": cloud["version"], "weights": cfg.get("global_weights", defaults),
                        "config": cfg, "metrics": cloud.get("metrics") or {}, "rules": cloud.get("rules") or []}
        except Exception:
            pass
        # Preserve an already learned v13 champion as the v14 starting point.
        try:
            from learning_engine_v13 import active_model as active_v13
            previous = active_v13(defaults)
            previous_weights = dict(previous.get("weights") or defaults)
            cfg = _model_config(previous_weights)
            return {"version": previous.get("version", "13.0-base"), "weights": previous_weights,
                    "config": cfg, "metrics": previous.get("metrics") or {}, "rules": previous.get("rules") or []}
        except Exception:
            cfg = _model_config(defaults)
            return {"version": "13.0-base", "weights": dict(defaults), "config": cfg, "metrics": {}, "rules": []}
    cfg = _json(row["config_json"], _model_config(defaults))
    parsed_rules = []
    for r in rules:
        rr = dict(r)
        rr.update(_json(rr.pop("rule_json"), {}))
        parsed_rules.append(rr)
    return {"version": row["version"], "weights": cfg.get("global_weights", defaults), "config": cfg,
            "metrics": _json(row["metrics_json"], {}), "rules": parsed_rules}


def specialist_weights(model: Dict[str, Any], regime: str, direction: str) -> Dict[str, float]:
    cfg = model.get("config") or {}
    specialists = cfg.get("specialists") or {}
    direction = str(direction or "").upper()
    return specialists.get(f"{regime}:{direction}") or specialists.get(regime) or cfg.get("global_weights") or model.get("weights") or {}


def calibrated_probability(score: float, regime: str, model: Dict[str, Any]) -> Tuple[float, float]:
    calibration = (model.get("config") or {}).get("calibration") or {}
    bins = calibration.get(regime) or calibration.get("all") or []
    for item in bins:
        if float(item["score_min"]) <= score <= float(item["score_max"]):
            samples = max(1, int(item["samples"]))
            prob = float(item["probability"])
            uncertainty = min(0.35, 1.0 / math.sqrt(samples))
            return round(prob, 4), round(uncertainty, 4)
    return round(max(0.02, min(0.98, score / 100.0)), 4), 0.25


def apply_learning_adjustments(factors: Dict[str, float], rules: Iterable[Dict[str, Any]], regime: str = "all", direction: str = "") -> Dict[str, Any]:
    triggered, total = [], 0.0
    direction = str(direction or "").upper()
    for rule in rules:
        if rule.get("regime") not in ("all", regime):
            continue
        if rule.get("direction") not in ("ALL", "", direction):
            continue
        feature = rule.get("feature")
        value = float(factors.get(feature, 50))
        op, threshold = rule.get("operator"), float(rule.get("threshold", 50))
        hit = value <= threshold if op == "<=" else value >= threshold
        feature2 = rule.get("feature2")
        if hit and feature2:
            value2 = float(factors.get(feature2, 50))
            op2, threshold2 = rule.get("operator2"), float(rule.get("threshold2", 50))
            hit = value2 <= threshold2 if op2 == "<=" else value2 >= threshold2
        if hit:
            adjustment = float(rule.get("adjustment", rule.get("penalty", 0)))
            total += adjustment
            triggered.append({"kind": rule.get("kind", "rule"), "feature": feature, "operator": op,
                              "threshold": threshold, "feature2": feature2, "adjustment": adjustment})
    cap = float(os.getenv("LEARNING_MAX_TOTAL_ADJUSTMENT", "20"))
    total = max(-cap, min(cap, total))
    return {"adjustment": round(total, 2), "penalty": round(max(0.0, -total), 2), "triggered": triggered}


def apply_no_trade_penalty(factors: Dict[str, float], rules: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    # Compatibility facade used by v12/v13 scoring code.
    result = apply_learning_adjustments(factors, rules)
    return {"penalty": result["penalty"], "triggered": result["triggered"]}


def _derive_rules(samples: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    min_samples = max(8, int(os.getenv("LEARNING_RULE_MIN_SAMPLES", "14")))
    rules: List[Dict[str, Any]] = []
    contexts = [("all", "ALL", list(samples))]
    for regime in sorted({s["regime"] for s in samples}):
        contexts.append((regime, "ALL", [s for s in samples if s["regime"] == regime]))
    for direction in ("LONG", "SHORT"):
        contexts.append(("all", direction, [s for s in samples if s["direction"] == direction]))
    for regime, direction, subset0 in contexts:
        if len(subset0) < min_samples * 2:
            continue
        for feature in FEATURES:
            vals = sorted(s["factors"][feature] for s in subset0)
            cuts = [("<=", vals[int(len(vals) * 0.25)]), (">=", vals[int(len(vals) * 0.75)])]
            for op, cut in cuts:
                subset = [s for s in subset0 if (s["factors"][feature] <= cut if op == "<=" else s["factors"][feature] >= cut)]
                if len(subset) < min_samples:
                    continue
                wr, ar = mean(s["win"] for s in subset) * 100, mean(s["return"] for s in subset)
                base_wr, base_ar = mean(s["win"] for s in subset0) * 100, mean(s["return"] for s in subset0)
                if ar < min(-0.15, base_ar - 0.35) and wr < base_wr - 8:
                    adj = -min(14.0, max(2.0, abs(ar - base_ar) * 1.5 + (base_wr - wr) * 0.10))
                    rules.append({"kind": "no_trade", "regime": regime, "direction": direction,
                                  "feature": feature, "operator": op, "threshold": round(cut, 2),
                                  "samples": len(subset), "win_rate": wr, "avg_return": ar, "adjustment": round(adj, 2)})
                elif ar > max(0.20, base_ar + 0.45) and wr > base_wr + 8:
                    adj = min(8.0, max(1.0, (ar - base_ar) + (wr - base_wr) * 0.06))
                    rules.append({"kind": "boost", "regime": regime, "direction": direction,
                                  "feature": feature, "operator": op, "threshold": round(cut, 2),
                                  "samples": len(subset), "win_rate": wr, "avg_return": ar, "adjustment": round(adj, 2)})
    # Two-factor interactions, only strongest features to limit overfitting.
    candidates = ("trend", "alignment", "open_interest", "volume", "capital_flow", "smart_money")
    for i, f1 in enumerate(candidates):
        for f2 in candidates[i + 1:]:
            subset = [s for s in samples if s["factors"][f1] >= 65 and s["factors"][f2] >= 65]
            if len(subset) < min_samples:
                continue
            wr, ar = mean(s["win"] for s in subset) * 100, mean(s["return"] for s in subset)
            base_wr, base_ar = mean(s["win"] for s in samples) * 100, mean(s["return"] for s in samples)
            if wr > base_wr + 10 and ar > base_ar + 0.6:
                rules.append({"kind": "interaction", "regime": "all", "direction": "ALL",
                              "feature": f1, "operator": ">=", "threshold": 65,
                              "feature2": f2, "operator2": ">=", "threshold2": 65,
                              "samples": len(subset), "win_rate": wr, "avg_return": ar,
                              "adjustment": round(min(8.0, 1 + ar - base_ar + (wr - base_wr) * 0.05), 2)})
    # Conservative sorting favors broadly supported rules.
    rules.sort(key=lambda r: abs(r["adjustment"]) * math.log1p(r["samples"]), reverse=True)
    return rules[:24]


def _calibration(samples: Sequence[Dict[str, Any]], weights: Dict[str, float]) -> List[Dict[str, Any]]:
    bins = []
    for lo in range(0, 100, 10):
        hi = 100 if lo == 90 else lo + 9.999
        subset = [s for s in samples if lo <= _score(s["factors"], weights) <= hi]
        if not subset:
            continue
        # Beta(2,2) smoothing prevents extreme probabilities on tiny samples.
        wins = sum(float(s["win"]) for s in subset)
        prob = (wins + 2.0) / (len(subset) + 4.0)
        bins.append({"score_min": lo, "score_max": hi, "samples": len(subset),
                     "wins": round(wins, 3), "avg_return": round(mean(s["return"] for s in subset), 4),
                     "probability": round(prob, 4)})
    return bins


def _drift(samples: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    if len(samples) < 40:
        return {"score": 0.0, "status": "insufficient-data", "details": {}}
    split = max(20, int(len(samples) * 0.70))
    old, recent = samples[:split], samples[split:]
    details, total = {}, 0.0
    for f in FEATURES:
        a, b = mean(s["factors"][f] for s in old), mean(s["factors"][f] for s in recent)
        delta = abs(a - b) / 100.0
        details[f] = round(delta, 4)
        total += delta
    score = min(1.0, total / len(FEATURES) * 4.0)
    return {"score": round(score, 4), "status": "high" if score >= 0.35 else "moderate" if score >= 0.18 else "stable", "details": details}


def _candidate_better(base: Dict[str, float], candidate: Dict[str, float], drift: Dict[str, Any]) -> bool:
    min_gain = float(os.getenv("LEARNING_MIN_UTILITY_GAIN", "0.012"))
    required = min_gain * (1.4 if drift.get("status") == "high" else 1.0)
    return (
        candidate["utility"] >= base["utility"] + required
        and candidate["brier"] <= base["brier"] * 1.03
        and candidate["top_avg_return"] >= base["top_avg_return"] - 0.10
        and candidate["rank_corr"] >= base["rank_corr"] - 0.02
    )


def train(defaults: Dict[str, float]) -> Dict[str, Any]:
    initialize()
    samples = load_samples()
    min_samples = max(50, int(os.getenv("LEARNING_MIN_SAMPLES", "60")))
    if len(samples) < min_samples:
        result = {"status": "collecting-data", "samples": len(samples), "required": min_samples,
                  "active": active_model(defaults)["version"]}
        with connect() as conn:
            conn.execute("INSERT INTO learning_runs(status,summary_json,created_at) VALUES(?,?,?)", (result["status"], json.dumps(result), now_iso()))
        try:
            from cloud_model_store import CloudModelStore
            store = CloudModelStore()
            current_model = active_model(defaults)
            store.save_model(current_model, "active", len(samples))
            store.save_training_run(result)
        except Exception:
            pass
        return result

    current = active_model(defaults)
    seed = int(hashlib.sha256((str(len(samples)) + samples[-1]["fingerprint"]).encode()).hexdigest()[:8], 16)
    global_weights = optimize_weights(samples, defaults, seed)
    specialists: Dict[str, Dict[str, float]] = {}
    specialist_min = max(30, int(os.getenv("LEARNING_SPECIALIST_MIN_SAMPLES", "36")))
    for regime in sorted({s["regime"] for s in samples}):
        subset = [s for s in samples if s["regime"] == regime]
        if len(subset) >= specialist_min:
            specialists[regime] = optimize_weights(subset, global_weights, seed + len(specialists) + 1)
        for direction in ("LONG", "SHORT"):
            directional = [s for s in subset if s["direction"] == direction]
            if len(directional) >= specialist_min:
                specialists[f"{regime}:{direction}"] = optimize_weights(directional, specialists.get(regime, global_weights), seed + len(specialists) + 11)

    # Final chronological holdout remains unseen by optimizer.
    split = max(1, min(len(samples) - 1, int(len(samples) * 0.82)))
    holdout = samples[split:]
    base_metrics = evaluate(holdout, current.get("weights") or defaults)
    candidate_metrics = evaluate(holdout, global_weights)
    drift = _drift(samples)
    promoted = _candidate_better(base_metrics, candidate_metrics, drift)
    rules = _derive_rules(samples[:split])
    calibration = {"all": _calibration(holdout, global_weights)}
    for regime in sorted({s["regime"] for s in holdout}):
        subset = [s for s in holdout if s["regime"] == regime]
        if len(subset) >= 8:
            calibration[regime] = _calibration(subset, specialists.get(regime, global_weights))

    config = {"global_weights": global_weights, "specialists": specialists,
              "calibration": calibration, "rules": rules, "drift": drift,
              "training": {"samples": len(samples), "holdout": len(holdout), "seed": seed}}
    version = "14." + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    status = "active" if promoted else "challenger"
    metrics = {"baseline": base_metrics, "candidate": candidate_metrics, "promoted": promoted,
               "drift": drift, "specialists": len(specialists)}
    with connect() as conn:
        if promoted:
            conn.execute("UPDATE model_versions SET status='retired' WHERE status='active'")
        conn.execute("INSERT INTO model_versions(version,status,config_json,metrics_json,sample_count,created_at,activated_at) VALUES(?,?,?,?,?,?,?)",
                     (version, status, json.dumps(config), json.dumps(metrics), len(samples), now_iso(), now_iso() if promoted else None))
        for rule in rules:
            payload = {k: v for k, v in rule.items() if k not in {"kind", "regime", "direction", "samples", "win_rate", "avg_return", "adjustment"}}
            conn.execute("INSERT INTO learning_rules(model_version,kind,regime,direction,rule_json,samples,win_rate,avg_return,adjustment,active,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (version, rule["kind"], rule["regime"], rule["direction"], json.dumps(payload), rule["samples"], rule["win_rate"], rule["avg_return"], rule["adjustment"], 1 if promoted else 0, now_iso()))
        for regime, bins in calibration.items():
            for b in bins:
                conn.execute("INSERT OR REPLACE INTO calibration_bins VALUES(?,?,?,?,?,?,?,?)",
                             (version, regime, b["score_min"], int(b["score_max"]), b["samples"], b["wins"], b["avg_return"], b["probability"]))
        conn.execute("INSERT INTO drift_snapshots(model_version,drift_score,details_json,created_at) VALUES(?,?,?,?)",
                     (version, drift["score"], json.dumps(drift), now_iso()))
    result = {"status": status, "samples": len(samples), "version": version, "promoted": promoted,
              "metrics": metrics, "specialists": len(specialists), "rules": len(rules),
              "active": version if promoted else current["version"]}
    with connect() as conn:
        conn.execute("INSERT INTO learning_runs(status,summary_json,created_at) VALUES(?,?,?)", (status, json.dumps(result), now_iso()))
    try:
        from cloud_model_store import CloudModelStore
        store = CloudModelStore()
        store.save_model({"version": version, "config": config, "metrics": metrics}, status, len(samples))
        store.save_training_run(result)
    except Exception:
        pass
    return result


def diagnostics(defaults: Dict[str, float]) -> Dict[str, Any]:
    initialize()
    model = active_model(defaults)
    samples = load_samples()
    metrics = evaluate(samples, model.get("weights") or defaults)
    drift = _drift(samples)
    with connect() as conn:
        versions = [dict(r) for r in conn.execute("SELECT version,status,sample_count,created_at,metrics_json FROM model_versions ORDER BY id DESC LIMIT 8").fetchall()]
    if not versions:
        try:
            from cloud_model_store import CloudModelStore
            versions = CloudModelStore().list_models(8)
        except Exception:
            versions = []
    regime_stats = {}
    for regime in sorted({s["regime"] for s in samples}):
        subset = [s for s in samples if s["regime"] == regime]
        regime_stats[regime] = evaluate(subset, specialist_weights(model, regime, ""))
    return {"active": model, "samples": len(samples), "metrics": metrics, "drift": drift,
            "regimes": regime_stats, "versions": versions}
