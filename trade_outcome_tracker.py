from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from trade_market_client import create_trade_market_client

logger = logging.getLogger("trade_outcome_tracker")

DB_PATH = Path("data") / "trade_outcomes.db"
HORIZONS = {"1h": 1, "4h": 4, "24h": 24, "72h": 72}
PRIMARY_READY_HORIZON = os.getenv("LEARNING_READY_HORIZON", "1h").lower()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso() -> str:
    return utc_now().isoformat()


def _parse_dt(value: Any) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_trade_outcomes() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tracked_signals (
                fingerprint TEXT PRIMARY KEY,
                cloud_id TEXT,
                symbol TEXT NOT NULL,
                direction TEXT,
                timeframe TEXT,
                entry_price REAL,
                stop REAL,
                tp1 REAL,
                tp2 REAL,
                tp3 REAL,
                score REAL,
                probability REAL,
                ai_score REAL,
                ai_tier TEXT,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_outcomes (
                fingerprint TEXT NOT NULL,
                horizon TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                price REAL,
                return_percent REAL,
                result_label TEXT,
                PRIMARY KEY(fingerprint, horizon)
            )
        """)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tracked_signals)").fetchall()}
        additions = {
            "cloud_id": "TEXT", "ai_score": "REAL", "ai_tier": "TEXT", "timeframe": "TEXT"
        }
        for column, sql_type in additions.items():
            if column not in columns:
                conn.execute(f"ALTER TABLE tracked_signals ADD COLUMN {column} {sql_type}")


def _entry_from_signal(signal: dict[str, Any]) -> float | None:
    value = signal.get("entryPrice", signal.get("entry_price"))
    if value is not None:
        try:
            return float(value)
        except Exception:
            return None
    text = str(signal.get("entryText") or "").replace(">", "").strip()
    if "–" in text:
        try:
            a, b = text.split("–", 1)
            return (float(a) + float(b)) / 2
        except Exception:
            return None
    try:
        return float(text)
    except Exception:
        return None


def register_trade_signal(signal: dict[str, Any], cloud_id: str | None = None, preserve_created_at: bool = False) -> bool:
    """Idempotently cache a cloud/local signal for outcome processing."""
    initialize_trade_outcomes()
    entry = _entry_from_signal(signal)
    fingerprint = str(signal.get("fingerprint") or "")
    if entry is None or not fingerprint:
        return False
    symbol = str(signal.get("symbol") or "").upper()
    direction = str(signal.get("direction") or signal.get("signal_direction") or "")
    timeframe = str(signal.get("timeframe") or signal.get("interval") or "unknown").lower()
    created_at = str(signal.get("created_at") or signal.get("signal_created_at") or utc_iso()) if preserve_created_at else utc_iso()

    # Dedupe only genuinely new signals. Cloud recovery must never be discarded.
    if not preserve_created_at:
        dedupe_minutes = max(1, int(os.getenv("LEARNING_SIGNAL_DEDUPE_MINUTES", "20")))
        cutoff = (utc_now() - timedelta(minutes=dedupe_minutes)).isoformat()
        with get_connection() as conn:
            duplicate = conn.execute(
                "SELECT 1 FROM tracked_signals WHERE symbol=? AND direction=? "
                "AND COALESCE(timeframe,'unknown')=? AND created_at>=? LIMIT 1",
                (symbol, direction, timeframe, cutoff),
            ).fetchone()
            if duplicate:
                return False

    with get_connection() as conn:
        conn.execute("""
            INSERT INTO tracked_signals (
                fingerprint,cloud_id,symbol,direction,timeframe,entry_price,stop,tp1,tp2,tp3,
                score,probability,ai_score,ai_tier,created_at,payload_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(fingerprint) DO UPDATE SET
                cloud_id=COALESCE(excluded.cloud_id,tracked_signals.cloud_id),
                payload_json=excluded.payload_json
        """, (
            fingerprint, cloud_id, symbol, direction, timeframe, entry,
            signal.get("stop", signal.get("stop_loss")), signal.get("tp1", signal.get("target_price")),
            signal.get("tp2"), signal.get("tp3"), signal.get("score", signal.get("signal_score")),
            signal.get("probability", signal.get("signal_confidence")), signal.get("aiScore"),
            signal.get("aiTier"), created_at, json.dumps(signal, ensure_ascii=False, default=str),
        ))
    return True


def sync_pending_from_cloud(limit: int = 2000) -> int:
    """Rebuild the ephemeral local tracker from durable Supabase rows."""
    try:
        from cloud_learning_store import CloudLearningStore
        rows = CloudLearningStore().pending(limit=limit)
    except Exception:
        logger.exception("Cloud pending sync failed")
        return 0
    imported = 0
    for row in rows:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        features = row.get("features") if isinstance(row.get("features"), dict) else {}
        fingerprint = str(metadata.get("fingerprint") or row.get("id") or "")
        payload = dict(features)
        payload.update({
            "fingerprint": fingerprint,
            "symbol": row.get("symbol"),
            "timeframe": row.get("timeframe"),
            "direction": row.get("signal_direction"),
            "entryPrice": row.get("entry_price") or row.get("market_price_at_signal"),
            "stop": row.get("stop_loss"),
            "tp1": row.get("target_price"),
            "tp2": metadata.get("tp2"),
            "tp3": metadata.get("tp3"),
            "score": row.get("signal_score"),
            "probability": row.get("signal_confidence"),
            "aiScore": metadata.get("ai_score"),
            "aiTier": metadata.get("ai_tier"),
            "signal_created_at": row.get("signal_created_at") or row.get("created_at"),
        })
        if register_trade_signal(payload, cloud_id=str(row.get("id") or ""), preserve_created_at=True):
            imported += 1
    return imported



def _historical_prices(client: Any, symbol: str) -> list[tuple[datetime, float, float, float]]:
    """Load up to ~83 hours of 5m candles for accurate restart recovery."""
    try:
        rows = client.klines(symbol, "5m", 1000)
        result: list[tuple[datetime, float, float, float]] = []
        for item in rows or []:
            ts = datetime.fromtimestamp(float(item[0]) / 1000.0, tz=timezone.utc)
            result.append((ts, float(item[4]), float(item[2]), float(item[3])))
        return sorted(result, key=lambda x: x[0])
    except Exception as exc:
        logger.warning("Historical candle recovery unavailable for %s: %s", symbol, exc)
        return []


def _price_at(candles: list[tuple[datetime, float, float, float]], target: datetime) -> float | None:
    if not candles:
        return None
    eligible = [row for row in candles if row[0] <= target]
    return eligible[-1][1] if eligible else candles[0][1]

def _label(row: sqlite3.Row, price: float) -> str:
    direction = str(row["direction"] or "").upper()
    stop, tp1, tp2, tp3 = row["stop"], row["tp1"], row["tp2"], row["tp3"]
    if direction in {"LONG", "LONG_BIAS"}:
        if tp3 and price >= tp3: return "TP3"
        if tp2 and price >= tp2: return "TP2"
        if tp1 and price >= tp1: return "TP1"
        if stop and price <= stop: return "SL"
    elif direction in {"SHORT", "SHORT_BIAS"}:
        if tp3 and price <= tp3: return "TP3"
        if tp2 and price <= tp2: return "TP2"
        if tp1 and price <= tp1: return "TP1"
        if stop and price >= stop: return "SL"
    return "OPEN"


def _cloud_result_payload(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any] | None:
    outcomes = conn.execute(
        "SELECT * FROM trade_outcomes WHERE fingerprint=? ORDER BY observed_at",
        (row["fingerprint"],),
    ).fetchall()
    if not outcomes:
        return None
    returns = {str(o["horizon"]): float(o["return_percent"] or 0) for o in outcomes}
    prices = {str(o["horizon"]): float(o["price"] or 0) for o in outcomes}
    labels = {str(o["horizon"]): str(o["result_label"] or "OPEN") for o in outcomes}
    latest = outcomes[-1]
    ready = PRIMARY_READY_HORIZON in returns or len(returns) > 0
    return {
        "market_price_after": float(latest["price"] or 0),
        "price_change_pct": float(latest["return_percent"] or 0),
        "outcome": str(latest["result_label"] or "OPEN"),
        "outcome_score": 1.0 if float(latest["return_percent"] or 0) > 0 else 0.0,
        "real_result": {
            "returns": returns,
            "prices": prices,
            "labels": labels,
            "latest_horizon": str(latest["horizon"]),
            "return_percent": float(latest["return_percent"] or 0),
            "target": 1 if float(latest["return_percent"] or 0) > 0 else 0,
            "success": float(latest["return_percent"] or 0) > 0,
            "updated_at": utc_iso(),
        },
        "resolved_at": str(latest["observed_at"]) if ready else None,
        "training_status": "ready" if ready else "pending",
    }


def _sync_row_to_cloud(conn: sqlite3.Connection, row: sqlite3.Row) -> bool:
    payload = _cloud_result_payload(conn, row)
    if payload is None:
        return False
    try:
        from cloud_learning_store import CloudLearningStore
        store = CloudLearningStore()
        if row["cloud_id"]:
            return store.update_by_id(str(row["cloud_id"]), payload)
        return store.update_outcome(str(row["fingerprint"]), payload)
    except Exception:
        logger.exception("Cloud outcome sync failed: %s", row["fingerprint"])
        return False


def update_trade_outcomes() -> dict[str, Any]:
    """Recover cloud rows, calculate due horizons and durably sync every result."""
    initialize_trade_outcomes()
    imported = sync_pending_from_cloud()
    client = create_trade_market_client()
    now = utc_now()
    updated = 0
    cloud_synced = 0
    errors: list[str] = []
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM tracked_signals ORDER BY created_at").fetchall()
        for row in rows:
            try:
                created = _parse_dt(row["created_at"])
            except Exception as exc:
                errors.append(f"fingerprint={row['fingerprint']}: bad created_at: {exc}")
                continue
            candles = _historical_prices(client, row["symbol"])
            row_changed = False
            for horizon, hours in HORIZONS.items():
                if now < created + timedelta(hours=hours):
                    continue
                if conn.execute(
                    "SELECT 1 FROM trade_outcomes WHERE fingerprint=? AND horizon=?",
                    (row["fingerprint"], horizon),
                ).fetchone():
                    continue
                try:
                    target_time = created + timedelta(hours=hours)
                    historical_price = _price_at(candles, target_time)
                    if historical_price is not None:
                        price = float(historical_price)
                    else:
                        ticker = client.ticker_24h(row["symbol"])
                        price = float(ticker.get("lastPrice") or ticker.get("last_price") or 0)
                    entry = float(row["entry_price"] or 0)
                    if price <= 0 or entry <= 0:
                        raise ValueError(f"invalid price entry={entry} current={price}")
                    raw_ret = (price - entry) / entry * 100
                    direction = str(row["direction"] or "").upper()
                    signed_ret = raw_ret if direction in {"LONG", "LONG_BIAS"} else -raw_ret
                    conn.execute(
                        "INSERT OR IGNORE INTO trade_outcomes VALUES (?,?,?,?,?,?)",
                        (row["fingerprint"], horizon, now.isoformat(), price, signed_ret, _label(row, price)),
                    )
                    updated += 1
                    row_changed = True
                except Exception as exc:
                    error = f"symbol={row['symbol']}, horizon={horizon}, fingerprint={row['fingerprint']}, error={type(exc).__name__}: {exc}"
                    logger.warning("Outcome tracker error: %s", error)
                    errors.append(error)
            # Retry cloud synchronization even when local result already existed before restart.
            if row_changed or conn.execute(
                "SELECT 1 FROM trade_outcomes WHERE fingerprint=? LIMIT 1", (row["fingerprint"],)
            ).fetchone():
                if _sync_row_to_cloud(conn, row):
                    cloud_synced += 1
    return {"imported": imported, "updated": updated, "cloud_synced": cloud_synced, "errors": errors}


def get_trade_performance() -> list[dict[str, Any]]:
    initialize_trade_outcomes()
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT horizon, COUNT(*) count, AVG(return_percent) avg_return,
                   SUM(CASE WHEN return_percent > 0 THEN 1 ELSE 0 END) wins,
                   SUM(CASE WHEN result_label LIKE 'TP%' THEN 1 ELSE 0 END) tp_hits,
                   SUM(CASE WHEN result_label = 'SL' THEN 1 ELSE 0 END) sl_hits
            FROM trade_outcomes GROUP BY horizon
            ORDER BY CASE horizon WHEN '1h' THEN 1 WHEN '4h' THEN 2 WHEN '24h' THEN 3 WHEN '72h' THEN 4 ELSE 5 END
        """).fetchall()
    return [dict(row) for row in rows]


def persist_trade_signal(signal: dict[str, Any], source: str = "trade") -> str | None:
    """Durably save a signal to Supabase first, then cache it locally."""
    fingerprint = str(signal.get("fingerprint") or "")
    if not fingerprint:
        return None
    created_at = str(signal.get("signal_created_at") or utc_iso())
    resolve_after = (_parse_dt(created_at) + timedelta(hours=1)).isoformat()
    chronos = signal.get("chronos") or {}
    direction = str(signal.get("direction") or "").upper()
    chronos_probability = (
        chronos.get("probabilityUp")
        if direction in {"LONG", "LONG_BIAS", "BUY"}
        else chronos.get("probabilityDown")
    )
    payload = {
        "symbol": signal.get("symbol"),
        "timeframe": signal.get("timeframe") or signal.get("interval") or "unknown",
        "signal_type": source,
        "signal_direction": signal.get("direction"),
        "signal_score": signal.get("score"),
        "signal_confidence": signal.get("probability"),
        "entry_price": _entry_from_signal(signal),
        "target_price": signal.get("tp1"),
        "stop_loss": signal.get("stop"),
        "market_price_at_signal": _entry_from_signal(signal),
        "features": signal,
        "metadata": {
            "source": source,
            "fingerprint": fingerprint,
            "ai_score": signal.get("aiScore"),
            "ai_tier": signal.get("aiTier"),
            "tp2": signal.get("tp2"),
            "tp3": signal.get("tp3"),
            "quality_score": signal.get("qualityScore"),
            "calibrated_probability": signal.get("calibratedProbability"),
            "expected_value_pct": signal.get("expectedValuePct"),
            "expected_win_pct": signal.get("expectedWinPct"),
            "expected_loss_pct": signal.get("expectedLossPct"),
            "quality_decision": signal.get("qualityDecision"),
            "quality_passed": signal.get("qualityPassed"),
            "quality_adjustment": signal.get("qualityAdjustment"),
            "quality_rules": signal.get("qualityRules"),
            "positive_profile_hits": signal.get("positiveProfileHits"),
            "anti_profile_hits": signal.get("antiProfileHits"),
            "historical_probability": signal.get("historicalProbability"),
            "historical_evidence": signal.get("historicalEvidence"),
            "hedge_profile_version": signal.get("hedgeProfileVersion"),
            "chronos": chronos or None,
            "chronos_status": signal.get("chronosStatus"),
        },
        "signal_created_at": created_at,
        "resolve_after": resolve_after,
        "training_status": "pending",
        "quality_score": signal.get("qualityScore"),
        "calibrated_probability": signal.get("calibratedProbability"),
        "expected_value_pct": signal.get("expectedValuePct"),
        "quality_decision": signal.get("qualityDecision"),
        "hedge_profile_version": signal.get("hedgeProfileVersion"),
        "chronos_probability": chronos_probability,
        "chronos_return_pct": chronos.get("forecastReturnPct"),
        "chronos_agreement": chronos.get("directionAgreement"),
        "chronos_model": chronos.get("model"),
        "chronos_status": signal.get("chronosStatus"),
    }

    try:
        from cloud_learning_store import CloudLearningStore
        cloud_id = CloudLearningStore().save(payload)
    except Exception:
        logger.exception("Durable signal save failed: %s", fingerprint)
        cloud_id = None
    if cloud_id:
        register_trade_signal(signal, cloud_id=cloud_id)
    else:
        # Keep a local retryable cache, but return None so callers/logs know cloud failed.
        register_trade_signal(signal)
    return cloud_id
