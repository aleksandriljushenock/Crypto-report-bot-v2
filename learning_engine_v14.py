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
from core.sqlite_utils import connect as safe_sqlite_connect
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
_RESTORE_ATTEMPTED = False


def _runtime_env(name: str, default: Any) -> str:
    """Read V40 runtime model setting, falling back to ENV/default."""
    try:
        from model_control import runtime_env
        return runtime_env(name, default)
    except Exception:
        return os.getenv(name, str(default))


def _apply_operator_weight_policy(weights: Dict[str, float], defaults: Dict[str, float]) -> Dict[str, float]:
    try:
        from model_control import apply_weight_policy
        return apply_weight_policy(weights, defaults)
    except Exception:
        return dict(weights)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) else value
    except Exception:
        return default


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = safe_sqlite_connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def initialize() -> None:
    global _RESTORE_ATTEMPTED
    if not _RESTORE_ATTEMPTED:
        _RESTORE_ATTEMPTED = True
        try:
            from learning_checkpoint_manager import restore_checkpoint
            restore_checkpoint(DB_PATH)
        except Exception:
            pass
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


def _execution_samples() -> Dict[str, Dict[str, Any]]:
    """Load canonical filled+closed Paper outcomes keyed by signal fingerprint.

    V52 treats execution outcome as the primary learning target.  A 24h mark-to-market
    move is only a fallback for signals that were never executed.
    """
    if os.getenv("LEARNING_EXECUTION_TARGET_ENABLED", "true").lower() not in {"1", "true", "yes", "on"}:
        return {}
    try:
        from repositories.paper_repository import PaperRepository
        rows = PaperRepository().all_valid_closed_positions(None, ascending=True)
    except Exception:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        fp = str(row.get("fingerprint") or "")
        if not fp:
            continue
        try:
            notional = float(row.get("notional_usd") or 0.0)
            pnl = float(row.get("net_pnl") or 0.0)
            # Leverage-independent execution return. Using return-on-margin here
            # would make the learning target depend on chosen leverage rather than signal quality.
            return_pct = (pnl / notional * 100.0) if notional > 0 else 0.0
            entry = float(row.get("entry_price") or 0.0)
            stop = float(row.get("stop_price") or 0.0)
            risk_pct = abs(entry - stop) / entry * 100.0 if entry > 0 and stop > 0 else 0.0
            r_multiple = (return_pct / risk_pct) if risk_pct > 1e-9 else 0.0
        except Exception:
            continue
        out[fp] = {
            "return": max(-100.0, min(100.0, return_pct)),
            "net_pnl": pnl,
            "r_multiple": max(-20.0, min(20.0, r_multiple)),
            "win": 1.0 if pnl > 1e-9 else (0.0 if pnl < -1e-9 else 0.5),
            "close_reason": str(row.get("close_reason") or "UNKNOWN"),
            "opened_at": row.get("opened_at"),
            "closed_at": row.get("closed_at"),
            "source": "paper_execution",
        }
    return out


def _paper_learning_samples() -> List[Dict[str, Any]]:
    """Build complete learning rows directly from canonical closed Paper positions.

    This also covers executed signals that were never present in tracked_signals or
    learning_observations because a process restarted between persistence steps.
    """
    try:
        from repositories.paper_repository import PaperRepository
        rows = PaperRepository().all_valid_closed_positions(None, ascending=True)
    except Exception:
        return []
    result=[]
    for row in rows:
        payload=_json(row.get("signal_payload"), {}) or {}
        factors=payload.get("aiFactors") or {}
        fp=str(row.get("fingerprint") or payload.get("fingerprint") or "")
        if not fp or not all(k in factors for k in FEATURES):
            continue
        try:
            notional=float(row.get("notional_usd") or 0.0); pnl=float(row.get("net_pnl") or 0.0)
            ret=(pnl/notional*100.0) if notional>0 else 0.0
            entry=float(row.get("entry_price") or 0.0); stop=float(row.get("stop_price") or 0.0)
            risk=abs(entry-stop)/entry*100.0 if entry>0 and stop>0 else 0.0
            rmult=ret/risk if risk>1e-9 else 0.0
        except Exception:
            continue
        result.append({
            "fingerprint":fp,"symbol":row.get("symbol"),
            "timeframe":payload.get("timeframe") or payload.get("primaryTimeframe") or "multi_tf",
            "direction":_normalize_direction(row.get("side") or payload.get("direction")),
            "setup":str(payload.get("setup") or "NONE").upper(),
            "created_at":row.get("opened_at") or payload.get("signal_created_at") or row.get("closed_at") or now_iso(),
            "old_score":float(payload.get("aiScore") or payload.get("score") or 0),
            "factors":{k:float(factors.get(k,50)) for k in FEATURES},
            "returns":{}, "return":max(-100.0,min(100.0,ret)),
            "win":1.0 if pnl>1e-9 else (0.0 if pnl<-1e-9 else 0.5),
            "r_multiple":max(-20.0,min(20.0,rmult)), "net_pnl":pnl,
            "close_reason":str(row.get("close_reason") or "UNKNOWN"),
            "target_horizon":"execution","target_source":"paper_execution",
        })
    return result


def _feature_quality(samples: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Detect constant/near-constant features before optimization."""
    inactive, details = [], {}
    min_std = max(0.0, float(_runtime_env("LEARNING_FEATURE_MIN_STD", "0.75")))
    for key in FEATURES:
        vals = [float((s.get("factors") or {}).get(key, 50.0)) for s in samples]
        if not vals:
            inactive.append(key); details[key] = {"std": 0.0, "range": 0.0}; continue
        avg = sum(vals) / len(vals)
        var = sum((v-avg)**2 for v in vals) / max(1, len(vals)-1)
        std = math.sqrt(var); span = max(vals)-min(vals)
        details[key] = {"std": round(std, 4), "range": round(span, 4)}
        if std < min_std or span < min_std * 2:
            inactive.append(key)
    return {"inactive": inactive, "details": details}


def _dataset_health(samples: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Fail-closed promotion watchdog for stale, one-sided or low-quality datasets."""
    now = datetime.now(timezone.utc)
    dirs = {"LONG": 0, "SHORT": 0, "OTHER": 0}
    newest = None
    by_symbol_day: Dict[str, int] = {}
    for sample in samples:
        d = _normalize_direction(sample.get("direction"))
        dirs[d if d in dirs else "OTHER"] += 1
        try:
            dt = datetime.fromisoformat(str(sample.get("created_at") or "").replace("Z", "+00:00"))
            if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
            newest = dt if newest is None or dt > newest else newest
            key = f"{str(sample.get('symbol') or '').upper()}|{dt.date().isoformat()}"
            by_symbol_day[key] = by_symbol_day.get(key, 0) + 1
        except Exception:
            pass
    age_hours = (now-newest).total_seconds()/3600 if newest else 1e9
    min_direction = max(0, int(_runtime_env("LEARNING_MIN_DIRECTION_SAMPLES", "40")))
    max_stale = max(1.0, float(_runtime_env("LEARNING_MAX_DATA_AGE_HOURS", "72")))
    execution_count = sum(1 for s in samples if s.get('target_source') in {'paper_execution','shadow_execution_v57'})
    min_execution = max(0, int(_runtime_env("LEARNING_MIN_EXECUTION_SAMPLES_FOR_PROMOTION", "30")))
    reasons=[]
    if age_hours > max_stale: reasons.append("stale_dataset")
    # V54 trains direction specialists independently. One immature side must not
    # freeze a healthy champion for the other side; imbalance remains diagnostic.
    direction_warnings=[]
    if dirs['LONG'] and dirs['SHORT'] < min_direction: direction_warnings.append('short_underrepresented')
    if dirs['SHORT'] and dirs['LONG'] < min_direction: direction_warnings.append('long_underrepresented')
    if execution_count < min_execution: reasons.append("insufficient_execution_targets")
    concentration = max(by_symbol_day.values()) / max(1, len(samples)) if by_symbol_day else 0.0
    if concentration > float(_runtime_env("LEARNING_MAX_SYMBOL_DAY_CONCENTRATION", "0.12")):
        reasons.append("symbol_day_concentration")
    fq=_feature_quality(samples)
    return {"healthy": not reasons, "reasons": reasons, "newest_age_hours": round(age_hours,2),
            "directions": dirs, "execution_targets": execution_count, "concentration": round(concentration,4),
            "feature_quality": fq, "warnings": direction_warnings}


def _cloud_samples() -> List[Dict[str, Any]]:
    """Rebuild learning samples from persistent Supabase observations."""
    if os.getenv("LEARNING_CLOUD_RESTORE_ENABLED", "true").lower() not in {"1", "true", "yes", "on"}:
        return []
    try:
        from cloud_learning_store import CloudLearningStore
        rows = CloudLearningStore().resolved_rows(limit=int(_runtime_env("LEARNING_CLOUD_MAX_ROWS", "10000")))
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
            "timeframe": row.get("timeframe") or features.get("timeframe") or "unknown",
            "direction": _normalize_direction(row.get("signal_direction") or features.get("direction")),
            "setup": str(features.get("setup") or "NONE").upper(),
            "created_at": row.get("signal_created_at") or row.get("created_at") or now_iso(),
            "old_score": float(row.get("signal_score") or features.get("aiScore") or features.get("score") or 0),
            "factors": {k: float(factors.get(k, 50)) for k in FEATURES},
            "returns": returns,
        })
    return result


def _execution_v57_cloud_samples() -> List[Dict[str, Any]]:
    """Load first-hit shadow execution labels produced by V57 replay."""
    if os.getenv("EXECUTION_V57_LEARNING_ENABLED", "true").lower() not in {"1","true","yes","on"}:
        return []
    try:
        from cloud_client import get_supabase_client
        client=get_supabase_client(); out=[]; offset=0; cap=int(_runtime_env("EXECUTION_V57_LEARNING_MAX_ROWS","10000")); page=1000
        while len(out)<cap:
            rows=(client.table("execution_training_dataset_v57").select("*").eq("entry_status","filled").order("signal_created_at",desc=False).range(offset,min(offset+page-1,cap-1)).execute().data or [])
            if not rows: break
            for row in rows:
                outcome=str(row.get("outcome") or "").upper()
                if outcome in {"","UNRESOLVED","OPEN","NO_FILL"} or row.get("net_return_pct") is None: continue
                payload=_json(row.get("feature_payload"),{}) or {}; factors=payload.get("aiFactors") or payload.get("features") or {}
                if not all(k in factors for k in FEATURES): continue
                ret=max(-30.0,min(30.0,float(row.get("net_return_pct") or 0)))
                sample_type = str(row.get("sample_type") or "").upper()
                target_source = "paper_execution" if sample_type.startswith("PAPER_") else "shadow_execution_v57"
                out.append({"fingerprint":str(row.get("fingerprint") or row.get("shadow_id")),"symbol":row.get("symbol"),"timeframe":payload.get("timeframe") or payload.get("interval") or "multi","direction":_normalize_direction(row.get("direction")),"setup":str(row.get("setup") or payload.get("setup") or "NONE").upper(),"created_at":row.get("signal_created_at"),"old_score":float(payload.get("aiScore") or payload.get("score") or 0),"factors":{k:float(factors.get(k,50)) for k in FEATURES},"returns":{},"return":ret,"win":1.0 if ret>1e-9 else 0.0,"r_multiple":float(row.get("r_multiple") or 0),"target_horizon":"execution","target_source":target_source,"close_reason":outcome})
            if len(rows)<page: break
            offset+=len(rows)
        return out
    except Exception:
        return []

def _dedupe_samples(samples: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove near-duplicate signals to prevent one market burst dominating training."""
    window_minutes = max(1, int(os.getenv("LEARNING_DEDUPE_WINDOW_MINUTES", "20")))
    seen: dict[tuple[str, str, str, int], Dict[str, Any]] = {}
    for sample in sorted(samples, key=lambda x: str(x.get("created_at", ""))):
        try:
            dt = datetime.fromisoformat(str(sample.get("created_at", "")).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            bucket = int(dt.timestamp() // (window_minutes * 60))
        except Exception:
            bucket = hash(str(sample.get("fingerprint", "")))
        key = (
            str(sample.get("symbol") or "").upper(),
            str(sample.get("timeframe") or "unknown").lower(),
            str(sample.get("direction") or "").upper(),
            bucket,
        )
        current = seen.get(key)
        if current is None or len(sample.get("returns") or {}) > len(current.get("returns") or {}):
            seen[key] = sample
    return sorted(seen.values(), key=lambda x: str(x.get("created_at", "")))


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
            "timeframe": payload.get("timeframe") or payload.get("interval") or "unknown",
            "direction": _normalize_direction(row["direction"]),
            "setup": str(payload.get("setup") or "NONE").upper(),
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
    paper_direct = {str(x.get("fingerprint")): x for x in _paper_learning_samples() if x.get("fingerprint")}
    # Keep the richer market-history sample when available; execution target is
    # attached below. Add direct Paper samples only when the observation is absent.
    for fp, item in paper_direct.items():
        grouped.setdefault(fp, item)
    grouped_samples = _dedupe_samples(list(grouped.values()))
    # V57 replayed shadow executions are independent execution-labelled samples.
    # They are merged by fingerprint and override proxy labels.
    v57_exec={str(x.get("fingerprint")):x for x in _execution_v57_cloud_samples() if x.get("fingerprint")}
    for fp,item in v57_exec.items(): grouped[fp]=item
    grouped_samples = _dedupe_samples(list(grouped.values()))
    execution = _execution_samples()
    result = []
    target_horizon = str(_runtime_env("LEARNING_TARGET_HORIZON", "24h")).lower()
    if target_horizon not in HORIZONS:
        target_horizon = "24h"
    for item in grouped_samples:
        # Train and validate on exactly the same matured horizon. Mixing 1h-only
        # fresh samples with 72h mature samples changes the target definition over time.
        exec_target = execution.get(str(item.get("fingerprint") or ""))
        if item.get("target_source") == "shadow_execution_v57" and item.get("return") is not None:
            pass
        elif exec_target:
            item["return"] = float(exec_target["return"])
            item["win"] = float(exec_target["win"])
            item["r_multiple"] = float(exec_target.get("r_multiple") or 0.0)
            item["net_pnl"] = float(exec_target.get("net_pnl") or 0.0)
            item["close_reason"] = exec_target.get("close_reason")
            item["target_horizon"] = "execution"
            item["target_source"] = "paper_execution"
        else:
            if target_horizon not in item["returns"]:
                continue
            target_return = max(-30.0, min(30.0, float(item["returns"][target_horizon])))
            item["return"] = target_return
            item["win"] = 1.0 if target_return > 1e-9 else (0.0 if target_return < -1e-9 else 0.5)
            item["r_multiple"] = target_return / 3.0
            item["target_horizon"] = target_horizon
            item["target_source"] = "mark_to_market_fallback"
        item["regime"] = classify_regime(item["factors"])
        result.append(item)
    # Correlated bursts from one symbol/day are not independent observations.
    cluster_counts: Dict[str, int] = {}
    for item in result:
        try:
            dt=datetime.fromisoformat(str(item.get("created_at") or "").replace("Z","+00:00"))
            if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
            ck=f"{str(item.get('symbol') or '').upper()}|{dt.date().isoformat()}"
        except Exception:
            ck=str(item.get("fingerprint") or "")
        item["cluster_key"]=ck; cluster_counts[ck]=cluster_counts.get(ck,0)+1
    for item in result:
        item["cluster_size"]=cluster_counts.get(str(item.get("cluster_key") or ""),1)
    return sorted(result, key=lambda x: str(x["created_at"]))


def _score(factors: Dict[str, float], weights: Dict[str, float]) -> float:
    denom = sum(max(0.0, float(weights.get(k, 0.0))) for k in FEATURES)
    if denom <= 1e-12:
        return 50.0
    return max(0.0, min(100.0, sum(float(factors[k]) * max(0.0, float(weights.get(k, 0.0))) for k in FEATURES) / denom))


def _days_old(created_at: str) -> float:
    try:
        dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)
    except Exception:
        return 0.0


def _sample_weight(sample: Dict[str, Any]) -> float:
    half_life = max(2.0, float(_runtime_env("LEARNING_RECENCY_HALF_LIFE_DAYS", "30")))
    recency = 0.5 ** (_days_old(sample.get("created_at", "")) / half_life)
    cluster = max(1.0, float(sample.get("cluster_size") or 1.0))
    execution_boost = max(1.0, float(_runtime_env("LEARNING_EXECUTION_SAMPLE_WEIGHT", "4.0"))) if sample.get("target_source") in {"paper_execution","shadow_execution_v57"} else 1.0
    return recency * execution_boost / math.sqrt(cluster)


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
    gross_profit = sum(max(0.0, r) * w for r, w in zip(returns, ws))
    gross_loss = sum(abs(min(0.0, r)) * w for r, w in zip(returns, ws))
    profit_factor = gross_profit / gross_loss if gross_loss else (99.0 if gross_profit else 0.0)
    equity = peak = max_drawdown = 0.0
    for r in returns:
        equity += r
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    high_conf = [(p, w, r) for p, w, r in zip(probs, wins, returns) if p >= 0.70]
    high_conf_precision = (sum(w for _, w, _ in high_conf) / len(high_conf) * 100.0) if high_conf else 0.0
    # Utility rewards ranking and profitable selections, penalizes miscalibration and drawdown.
    utility = (
        0.28 * rank_corr
        + 0.22 * (top_wr / 100.0)
        + 0.22 * math.tanh(top_ret / 4.0)
        + 0.13 * math.tanh((profit_factor - 1.0) / 2.0)
        + 0.08 * (high_conf_precision / 100.0)
        - 0.12 * brier
        - 0.05 * math.tanh(max_drawdown / 15.0)
    )
    return {
        "samples": len(samples), "brier": round(brier, 6), "rank_corr": round(rank_corr, 5),
        "top_win_rate": round(top_wr, 2), "top_avg_return": round(top_ret, 4),
        "overall_win_rate": round(_weighted_mean(wins, ws) * 100, 2),
        "overall_avg_return": round(overall_ret, 4),
        "profit_factor": round(min(profit_factor, 99.0), 4),
        "max_drawdown_pct_points": round(max_drawdown, 4),
        "high_conf_samples": len(high_conf),
        "high_conf_precision": round(high_conf_precision, 2),
        "utility": round(utility, 6),
    }


def evaluate_routed_model(samples: Sequence[Dict[str, Any]], global_weights: Dict[str, float], specialists: Dict[str, Dict[str, float]]) -> Dict[str, float]:
    if not samples:
        return evaluate(samples, global_weights)
    scores=[]; wins=[]; returns=[]; ws=[]
    for sample in samples:
        regime=str(sample.get("regime") or "unknown")
        direction=_normalize_direction(sample.get("direction"))
        setup=str(sample.get("setup") or "NONE").upper()
        weights=(specialists.get(f"setup:{setup}:{direction}") or specialists.get(f"{regime}:{direction}")
                 or specialists.get(f"setup:{setup}") or specialists.get(regime) or global_weights)
        scores.append(_score(sample["factors"], weights)); wins.append(float(sample["win"])); returns.append(float(sample["return"])); ws.append(_sample_weight(sample))
    probs=[max(0.02,min(0.98,x/100.0)) for x in scores]
    brier=_weighted_mean([(p-w)**2 for p,w in zip(probs,wins)],ws)
    ranked=sorted(zip(scores,samples,ws),key=lambda x:x[0],reverse=True); top=ranked[:max(1,int(len(ranked)*0.25))]
    top_wr=_weighted_mean([float(x[1]["win"]) for x in top],[x[2] for x in top])*100
    top_ret=_weighted_mean([float(x[1]["return"]) for x in top],[x[2] for x in top]); rank_corr=_corr(scores,returns,ws)
    gp=sum(max(0,r)*w for r,w in zip(returns,ws)); gl=sum(abs(min(0,r))*w for r,w in zip(returns,ws)); pf=gp/gl if gl else (99.0 if gp else 0.0)
    equity=peak=dd=0.0
    for r in returns:
        equity+=r; peak=max(peak,equity); dd=max(dd,peak-equity)
    hi=[(p,w,r) for p,w,r in zip(probs,wins,returns) if p>=0.70]; hp=(sum(w for _,w,_ in hi)/len(hi)*100) if hi else 0.0
    utility=0.28*rank_corr+0.22*(top_wr/100)+0.22*math.tanh(top_ret/4)+0.13*math.tanh((pf-1)/2)+0.08*(hp/100)-0.12*brier-0.05*math.tanh(dd/15)
    return {"samples":len(samples),"brier":round(brier,6),"rank_corr":round(rank_corr,5),"top_win_rate":round(top_wr,2),"top_avg_return":round(top_ret,4),"overall_win_rate":round(_weighted_mean(wins,ws)*100,2),"overall_avg_return":round(_weighted_mean(returns,ws),4),"profit_factor":round(min(pf,99),4),"max_drawdown_pct_points":round(dd,4),"high_conf_samples":len(hi),"high_conf_precision":round(hp,2),"utility":round(utility,6)}


def walk_forward_folds(samples: Sequence[Dict[str, Any]], folds: int = 4) -> List[Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]]:
    n = len(samples)
    folds = max(2, min(folds, 8))
    min_train = max(20, int(n * 0.40))
    remaining = n - min_train
    if remaining < folds:
        return []
    step = max(1, remaining // folds)
    embargo = max(0, int(_runtime_env("LEARNING_WALK_FORWARD_EMBARGO_SAMPLES", "5")))
    result = []
    for i in range(folds):
        train_end = min_train + i * step
        val_start = min(n, train_end + embargo)
        val_end = n if i == folds - 1 else min(n, train_end + step)
        if val_end > val_start:
            result.append((list(samples[:train_end]), list(samples[val_start:val_end])))
    return result


def _bounded(weights: Dict[str, float], defaults: Dict[str, float]) -> Dict[str, float]:
    max_change = float(_runtime_env("LEARNING_MAX_WEIGHT_CHANGE", "0.35"))
    bounded = {}
    for k in FEATURES:
        base = float(defaults.get(k, 0.0))
        if base <= 0:
            bounded[k] = 0.0
        else:
            bounded[k] = round(max(base * (1 - max_change), min(base * (1 + max_change), float(weights.get(k, base)))), 5)
    return _apply_operator_weight_policy(bounded, defaults)


def optimize_weights(samples: Sequence[Dict[str, Any]], defaults: Dict[str, float], seed: int) -> Dict[str, float]:
    """Bounded deterministic random search, optimized on chronological folds."""
    if len(samples) < 20:
        return dict(defaults)
    rng = random.Random(seed)
    folds = walk_forward_folds(samples, max(5, int(_runtime_env("LEARNING_WALK_FORWARD_FOLDS", "5"))))
    if not folds:
        return dict(defaults)
    iterations = max(80, min(2500, int(_runtime_env("LEARNING_SEARCH_ITERATIONS", "800"))))
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
            if float(defaults.get(k, 0.0)) <= 0:
                trial[k] = 0.0
                continue
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
    # V52: Supabase is authoritative whenever cloud runtime is configured.  This
    # prevents an ephemeral/local SQLite champion from silently overriding or
    # surviving a different cloud champion after a restart.
    if os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_KEY"):
        try:
            from cloud_model_store import CloudModelStore
            cloud = CloudModelStore().load_active_model("learning-v14")
            if cloud and cloud.get("version"):
                cfg = dict(cloud.get("config") or _model_config(cloud.get("weights") or defaults))
                require_v52 = str(_runtime_env("LEARNING_REQUIRE_V53_TARGET_SCHEMA", "true")).lower() in {"1","true","yes","on"}
                if require_v52 and cfg.get("target_schema") != "execution_v54":
                    safe_cfg = _model_config(defaults)
                    safe_cfg.update({"target_schema":"execution_v54","calibration":{},"rules":[],"migration_reason":"legacy-target-model-blocked"})
                    effective = _apply_operator_weight_policy(dict(defaults), defaults)
                    return {"version":"53.0-safe-base","weights":effective,"learned_weights":dict(defaults),
                            "config":safe_cfg,"metrics":{"migration_blocked_version":cloud.get("version")},"rules":[],
                            "source":"v53-safe-base"}
                learned = dict(cfg.get("learned_global_weights") or cfg.get("global_weights") or cloud.get("weights") or defaults)
                effective = _apply_operator_weight_policy(learned, defaults)
                cfg["learned_global_weights"] = learned; cfg["global_weights"] = effective
                return {"version": cloud["version"], "weights": effective, "learned_weights": learned,
                        "config": cfg, "metrics": cloud.get("metrics") or {}, "rules": cloud.get("rules") or [],
                        "source": "cloud-authoritative"}
            if str(_runtime_env("LEARNING_REQUIRE_V53_TARGET_SCHEMA", "true")).lower() in {"1","true","yes","on"}:
                safe_cfg=_model_config(defaults); safe_cfg["target_schema"]="execution_v54"
                effective=_apply_operator_weight_policy(dict(defaults),defaults)
                return {"version":"53.0-safe-base","weights":effective,"learned_weights":dict(defaults),
                        "config":safe_cfg,"metrics":{},"rules":[],"source":"v53-safe-base"}
        except Exception:
            pass
    with connect() as conn:
        row = conn.execute("SELECT * FROM model_versions WHERE status='active' ORDER BY id DESC LIMIT 1").fetchone()
        rules = conn.execute("SELECT * FROM learning_rules WHERE active=1 AND model_version=? ORDER BY ABS(adjustment) DESC", (row["version"],)).fetchall() if row else []
    if not row:
        try:
            from cloud_model_store import CloudModelStore
            cloud = CloudModelStore().load_active_model()
            if cloud and cloud.get("version"):
                cfg = dict(cloud.get("config") or _model_config(cloud.get("weights") or defaults))
                learned = dict(cfg.get("learned_global_weights") or cfg.get("global_weights") or cloud.get("weights") or defaults)
                effective = _apply_operator_weight_policy(learned, defaults)
                cfg["learned_global_weights"] = learned
                cfg["global_weights"] = effective
                return {"version": cloud["version"], "weights": effective, "learned_weights": learned,
                        "config": cfg, "metrics": cloud.get("metrics") or {}, "rules": cloud.get("rules") or []}
        except Exception:
            pass
        # Preserve an already learned v13 champion as the v14 starting point.
        try:
            from learning_engine_v13 import active_model as active_v13
            previous = active_v13(defaults)
            previous_weights = dict(previous.get("weights") or defaults)
            cfg = _model_config(previous_weights)
            effective = _apply_operator_weight_policy(previous_weights, defaults)
            cfg["learned_global_weights"] = dict(previous_weights)
            cfg["global_weights"] = effective
            return {"version": previous.get("version", "13.0-base"), "weights": effective, "learned_weights": previous_weights,
                    "config": cfg, "metrics": previous.get("metrics") or {}, "rules": previous.get("rules") or []}
        except Exception:
            cfg = _model_config(defaults)
            effective = _apply_operator_weight_policy(dict(defaults), defaults)
            cfg["learned_global_weights"] = dict(defaults)
            cfg["global_weights"] = effective
            return {"version": "13.0-base", "weights": effective, "learned_weights": dict(defaults), "config": cfg, "metrics": {}, "rules": []}
    cfg = _json(row["config_json"], _model_config(defaults))
    parsed_rules = []
    for r in rules:
        rr = dict(r)
        rr.update(_json(rr.pop("rule_json"), {}))
        parsed_rules.append(rr)
    learned_weights = dict(cfg.get("global_weights", defaults))
    effective_weights = _apply_operator_weight_policy(learned_weights, defaults)
    cfg = dict(cfg)
    cfg["learned_global_weights"] = learned_weights
    cfg["global_weights"] = effective_weights
    return {"version": row["version"], "weights": effective_weights, "learned_weights": learned_weights, "config": cfg,
            "metrics": _json(row["metrics_json"], {}), "rules": parsed_rules}


def specialist_weights(model: Dict[str, Any], regime: str, direction: str, setup: str = "") -> Dict[str, float]:
    cfg = model.get("config") or {}
    specialists = cfg.get("specialists") or {}
    direction = _normalize_direction(direction)
    setup = str(setup or "").upper()
    selected = ((specialists.get(f"setup:{setup}:{direction}") if setup else None)
                or specialists.get(f"{regime}:{direction}")
                or (specialists.get(f"setup:{setup}") if setup else None)
                or specialists.get(regime) or cfg.get("global_weights") or model.get("weights") or {})
    defaults = model.get("learned_weights") or cfg.get("learned_global_weights") or selected
    return _apply_operator_weight_policy(dict(selected), dict(defaults))


def calibrated_probability(score: float, regime: str, model: Dict[str, Any], direction: str = "", setup: str = "") -> Tuple[float, float]:
    try:
        from model_control import calibration_valid as _calibration_valid
        if not _calibration_valid():
            return round(max(0.02, min(0.98, score / 100.0)), 4), 0.35
    except Exception:
        pass
    calibration = (model.get("config") or {}).get("calibration") or {}
    direction = _normalize_direction(direction)
    setup = str(setup or "").upper()
    bins = ((calibration.get(f"setup:{setup}:{direction}") if setup else None)
            or calibration.get(f"{regime}:{direction}")
            or (calibration.get(f"setup:{setup}") if setup else None)
            or calibration.get(regime) or calibration.get("all") or [])
    for item in bins:
        if float(item["score_min"]) <= score <= float(item["score_max"]):
            samples = max(1, int(item["samples"]))
            prob = float(item["probability"])
            uncertainty = min(0.35, 1.0 / math.sqrt(samples))
            return round(prob, 4), round(uncertainty, 4)
    return round(max(0.02, min(0.98, score / 100.0)), 4), 0.25


def apply_learning_adjustments(factors: Dict[str, float], rules: Iterable[Dict[str, Any]], regime: str = "all", direction: str = "") -> Dict[str, Any]:
    triggered, total = [], 0.0
    direction = _normalize_direction(direction)
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
    cap = float(_runtime_env("LEARNING_MAX_TOTAL_ADJUSTMENT", "20"))
    total = max(-cap, min(cap, total))
    return {"adjustment": round(total, 2), "penalty": round(max(0.0, -total), 2), "triggered": triggered}


def apply_no_trade_penalty(factors: Dict[str, float], rules: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    # Compatibility facade used by v12/v13 scoring code.
    result = apply_learning_adjustments(factors, rules)
    return {"penalty": result["penalty"], "triggered": result["triggered"]}


def _derive_rules(samples: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    min_samples = max(8, int(_runtime_env("LEARNING_RULE_MIN_SAMPLES", "14")))
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
    min_gain = float(_runtime_env("LEARNING_MIN_UTILITY_GAIN", "0.012"))
    required = min_gain * (1.4 if drift.get("status") == "high" else 1.0)
    min_holdout = max(30, int(_runtime_env("LEARNING_MIN_HOLDOUT_SAMPLES", "40")))
    return (
        candidate.get("samples", 0) >= min_holdout
        and candidate["utility"] >= base["utility"] + required
        and candidate["brier"] <= base["brier"] * 1.02
        and candidate["top_avg_return"] >= base["top_avg_return"]
        and candidate["rank_corr"] >= base["rank_corr"] - 0.01
        and candidate.get("profit_factor", 0.0) >= max(1.05, base.get("profit_factor", 0.0) * 0.98)
        and candidate.get("max_drawdown_pct_points", 999.0) <= base.get("max_drawdown_pct_points", 999.0) * 1.05 + 0.5
        and candidate.get("high_conf_precision", 0.0) >= base.get("high_conf_precision", 0.0) - 2.0
    )


def train(defaults: Dict[str, float]) -> Dict[str, Any]:
    initialize()
    samples = load_samples()
    min_samples = max(100, int(_runtime_env("LEARNING_MIN_SAMPLES", "200")))
    if len(samples) < min_samples:
        result = {"status": "collecting-data", "samples": len(samples), "required": min_samples,
                  "active": active_model(defaults)["version"]}
        # Collection progress is not a completed training run. Persist the active model
        # only at milestones to avoid polluting training_runs and excessive Supabase writes.
        milestone = max(10, int(os.getenv("LEARNING_COLLECTION_SAVE_STEP", "25")))
        if len(samples) == 0 or len(samples) % milestone == 0:
            try:
                current_model = active_model(defaults)
                from learning_checkpoint_manager import save_checkpoint
                save_checkpoint(DB_PATH, reason=f"collection-milestone-{len(samples)}")
            except Exception:
                pass
        return result

    health = _dataset_health(samples)
    feature_quality = health.get("feature_quality") or {}
    inactive_features = set(feature_quality.get("inactive") or [])
    training_defaults = {k: (0.0 if k in inactive_features else float(defaults[k])) for k in FEATURES}
    current = active_model(defaults)
    # Split BEFORE any optimization.  The final chronological holdout must remain
    # completely unseen by global and specialist optimizers.
    holdout_fraction = min(0.40, max(0.10, float(_runtime_env("LEARNING_HOLDOUT_FRACTION", "0.18"))))
    min_holdout = max(20, int(_runtime_env("LEARNING_MIN_HOLDOUT_SAMPLES", "30")))
    holdout_size = max(min_holdout, int(len(samples) * holdout_fraction))
    holdout_size = min(max(1, len(samples) - 1), holdout_size)
    split = len(samples) - holdout_size
    train_samples = samples[:split]
    holdout = samples[split:]
    seed_source = train_samples[-1]["fingerprint"] if train_samples else samples[0]["fingerprint"]
    seed = int(hashlib.sha256((str(len(train_samples)) + seed_source).encode()).hexdigest()[:8], 16)
    global_weights = optimize_weights(train_samples, training_defaults, seed)
    specialists: Dict[str, Dict[str, float]] = {}
    specialist_min = max(50, int(_runtime_env("LEARNING_SPECIALIST_MIN_SAMPLES", "80")))
    for regime in sorted({s["regime"] for s in train_samples}):
        subset = [s for s in train_samples if s["regime"] == regime]
        if len(subset) >= specialist_min:
            specialists[regime] = optimize_weights(subset, global_weights, seed + len(specialists) + 1)
        for direction in ("LONG", "SHORT"):
            directional = [s for s in subset if s["direction"] == direction]
            if len(directional) >= specialist_min:
                specialists[f"{regime}:{direction}"] = optimize_weights(directional, specialists.get(regime, global_weights), seed + len(specialists) + 11)

    # V53 setup specialists: PULLBACK and BREAKOUT have materially different
    # historical expectancy and must not share one weight vector.
    for setup_name in sorted({str(x.get("setup") or "NONE").upper() for x in train_samples}):
        setup_rows=[x for x in train_samples if str(x.get("setup") or "NONE").upper()==setup_name]
        if len(setup_rows)>=specialist_min:
            specialists[f"setup:{setup_name}"]=optimize_weights(setup_rows, global_weights, seed+len(specialists)+101)
        for direction in ("LONG","SHORT"):
            directional=[x for x in setup_rows if x.get("direction")==direction]
            if len(directional)>=specialist_min:
                specialists[f"setup:{setup_name}:{direction}"]=optimize_weights(directional, specialists.get(f"setup:{setup_name}",global_weights), seed+len(specialists)+151)

    current_config = current.get("config") or {}
    current_global = current.get("weights") or current_config.get("global_weights") or defaults
    current_specialists = current_config.get("specialists") or {}
    base_metrics = evaluate_routed_model(holdout, current_global, current_specialists)
    candidate_metrics = evaluate_routed_model(holdout, global_weights, specialists)
    drift = _drift(samples)
    promoted = _candidate_better(base_metrics, candidate_metrics, drift) and bool(health.get("healthy"))
    rules = _derive_rules(train_samples)
    calibration = {"all": _calibration(holdout, global_weights)}
    for regime in sorted({s["regime"] for s in holdout}):
        subset = [s for s in holdout if s["regime"] == regime]
        if len(subset) >= 8:
            calibration[regime] = _calibration(subset, specialists.get(regime, global_weights))
        for direction in ("LONG", "SHORT"):
            directional = [x for x in subset if x.get("direction") == direction]
            key = f"{regime}:{direction}"
            if len(directional) >= 8 and key in specialists:
                calibration[key] = _calibration(directional, specialists[key])

    for setup_name in sorted({str(x.get("setup") or "NONE").upper() for x in holdout}):
        subset=[x for x in holdout if str(x.get("setup") or "NONE").upper()==setup_name]
        skey=f"setup:{setup_name}"
        if len(subset)>=12 and skey in specialists:
            calibration[skey]=_calibration(subset,specialists[skey])
        for direction in ("LONG","SHORT"):
            drows=[x for x in subset if x.get("direction")==direction]
            key=f"setup:{setup_name}:{direction}"
            if len(drows)>=12 and key in specialists:
                calibration[key]=_calibration(drows,specialists[key])

    config = {"target_schema": "execution_v54", "global_weights": global_weights, "specialists": specialists,
              "calibration": calibration, "rules": rules, "drift": drift,
              "data_health": health, "inactive_features": sorted(inactive_features),
              "training": {"samples": len(samples), "train": len(train_samples), "holdout": len(holdout), "seed": seed,
                           "execution_targets": int(health.get("execution_targets") or 0)}}
    version = "14." + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    candidate_better = _candidate_better(base_metrics, candidate_metrics, drift) and bool(health.get("healthy"))
    metrics = {"baseline": base_metrics, "candidate": candidate_metrics, "candidate_better": candidate_better,
               "promoted": False, "drift": drift, "specialists": len(specialists), "data_health": health}

    # Persist locally as challenger first. Local activation happens only after the
    # authoritative cloud compare-and-promote commits (when cloud is configured).
    with connect() as conn:
        conn.execute("INSERT INTO model_versions(version,status,config_json,metrics_json,sample_count,created_at,activated_at) VALUES(?,?,?,?,?,?,?)",
                     (version, "challenger", json.dumps(config), json.dumps(metrics), len(samples), now_iso(), None))
        for rule in rules:
            payload = {k: v for k, v in rule.items() if k not in {"kind", "regime", "direction", "samples", "win_rate", "avg_return", "adjustment"}}
            conn.execute("INSERT INTO learning_rules(model_version,kind,regime,direction,rule_json,samples,win_rate,avg_return,adjustment,active,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (version, rule["kind"], rule["regime"], rule["direction"], json.dumps(payload), rule["samples"], rule["win_rate"], rule["avg_return"], rule["adjustment"], 0, now_iso()))
        for regime, bins in calibration.items():
            for b in bins:
                conn.execute("INSERT OR REPLACE INTO calibration_bins VALUES(?,?,?,?,?,?,?,?)",
                             (version, regime, b["score_min"], int(b["score_max"]), b["samples"], b["wins"], b["avg_return"], b["probability"]))
        conn.execute("INSERT INTO drift_snapshots(model_version,drift_score,details_json,created_at) VALUES(?,?,?,?)",
                     (version, drift["score"], json.dumps(drift), now_iso()))

    cloud_required = bool(os.getenv("SUPABASE_URL")) and bool(os.getenv("SUPABASE_SERVICE_KEY"))
    cloud_sync = "disabled" if not cloud_required else "pending"
    cloud_error = None
    promotion_committed = False
    expected_cloud_version = None
    store = None
    if cloud_required:
        try:
            from cloud_model_store import CloudModelStore
            store = CloudModelStore()
            cloud_current = store.load_active_model("learning-v14")
            expected_cloud_version = str(cloud_current.get("version")) if cloud_current and cloud_current.get("version") else None
            if not store.save_model({"model_name":"learning-v14","version": version, "config": config, "metrics": metrics}, "challenger", len(samples)):
                raise RuntimeError("candidate model persistence failed")
            cloud_sync = "candidate-saved"
        except Exception as exc:
            cloud_sync = "degraded"; cloud_error = f"{type(exc).__name__}: {exc}"

    if candidate_better:
        lease_ok = True
        try:
            from model_training_coordinator import lease_healthy
            lease_ok = lease_healthy()
        except Exception:
            lease_ok = True
        if not lease_ok:
            cloud_sync = "lease-lost"
            cloud_error = "distributed training lease was lost; promotion fenced"
        elif cloud_required:
            if store is not None and cloud_sync != "degraded":
                try:
                    lease_token=None; lease_generation=None
                    try:
                        from model_training_coordinator import lease_fence
                        lease_token,lease_generation=lease_fence()
                    except Exception:
                        pass
                    promotion_committed = store.promote_version_atomic("learning-v14", version, expected_cloud_version, lease_token=lease_token, lease_generation=lease_generation)
                    if not promotion_committed:
                        cloud_sync = "promotion-rejected"
                        cloud_error = "cloud champion changed or target promotion failed"
                    else:
                        verified = store.load_active_model("learning-v14")
                        if not verified or str(verified.get("version")) != version:
                            promotion_committed = False
                            cloud_sync = "promotion-verification-failed"
                            cloud_error = "cloud registry did not expose the promoted version as the sole active champion"
                        else:
                            cloud_sync = "ok"
                except Exception as exc:
                    cloud_sync = "degraded"; cloud_error = f"{type(exc).__name__}: {exc}"
        else:
            promotion_committed = True

    if promotion_committed:
        with connect() as conn:
            conn.execute("UPDATE model_versions SET status='retired' WHERE status='active' AND version<>?", (version,))
            conn.execute("UPDATE model_versions SET status='active', activated_at=? WHERE version=?", (now_iso(), version))
            conn.execute("UPDATE learning_rules SET active=0")
            conn.execute("UPDATE learning_rules SET active=1 WHERE model_version=?", (version,))
        try:
            from model_control import mark_calibration_valid
            mark_calibration_valid(True, updated_by="training")
        except Exception:
            pass

    model_status = "active" if promotion_committed else "challenger"
    metrics["promoted"] = promotion_committed
    result = {"status": "completed", "model_status": model_status,
              "samples": len(samples), "samples_total": len(samples),
              "samples_train": split, "samples_validation": len(holdout),
              "version": version, "promoted": promotion_committed, "candidate_better": candidate_better,
              "metrics": metrics, "specialists": len(specialists), "rules": len(rules),
              "feature_names": list(FEATURES),
              "target_horizon": "execution-first",
              "target_name": "paper_net_pnl_and_r_multiple",
              "data_health": health,
              "active": version if promotion_committed else current["version"],
              "cloud_sync": cloud_sync}
    if cloud_error:
        result["cloud_sync_error"] = cloud_error
    with connect() as conn:
        conn.execute("UPDATE model_versions SET metrics_json=? WHERE version=?", (json.dumps(metrics), version))
        conn.execute("INSERT INTO learning_runs(status,summary_json,created_at) VALUES(?,?,?)", ("completed", json.dumps(result), now_iso()))
    try:
        if store is not None:
            store.save_training_run(result)
        from learning_checkpoint_manager import save_checkpoint
        save_checkpoint(DB_PATH, reason=f"training-{model_status}-{version}")
    except Exception as exc:
        if result.get("cloud_sync") == "ok":
            result["cloud_sync"] = "degraded"
        result["cloud_sync_error"] = result.get("cloud_sync_error") or f"{type(exc).__name__}: {exc}"
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
