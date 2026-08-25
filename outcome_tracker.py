import json
import sqlite3
from core.sqlite_utils import connect as safe_sqlite_connect
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trade_market_client import create_trade_market_client
from historical_prices import historical_price_at


DATABASE_PATH = Path("data") / "alpha_outcomes.db"
HORIZONS = {"1h": 1, "24h": 24, "7d": 24 * 7, "30d": 24 * 30}


def _connect():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = safe_sqlite_connect(DATABASE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS predictions (
        project_key TEXT PRIMARY KEY, coin_id TEXT, symbol TEXT, score REAL,
        components_json TEXT, created_at TEXT, entry_price REAL)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS outcomes (
        project_key TEXT, horizon TEXT, observed_at TEXT, price REAL,
        return_percent REAL, PRIMARY KEY(project_key,horizon))"""
    )
    return conn


def register_prediction(project_key, coin_id, symbol, score, components, entry_price):
    now=datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute("DELETE FROM outcomes WHERE project_key=?", (project_key,))
        conn.execute(
            """INSERT INTO predictions(project_key,coin_id,symbol,score,components_json,created_at,entry_price)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(project_key) DO UPDATE SET
            coin_id=excluded.coin_id, symbol=excluded.symbol, score=excluded.score,
            components_json=excluded.components_json, created_at=excluded.created_at, entry_price=excluded.entry_price""",
            (project_key, coin_id, symbol, score, json.dumps(components, ensure_ascii=False), now, entry_price),
        )


def update_due_outcomes(timeout=20):
    now = datetime.now(timezone.utc)
    updated = 0
    errors = []
    with _connect() as conn:
        predictions = conn.execute("SELECT * FROM predictions WHERE coin_id IS NOT NULL AND entry_price IS NOT NULL").fetchall()
        for row in predictions:
            created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            for horizon, hours in HORIZONS.items():
                if now < created + timedelta(hours=hours):
                    continue
                exists = conn.execute("SELECT 1 FROM outcomes WHERE project_key=? AND horizon=?", (row["project_key"], horizon)).fetchone()
                if exists:
                    continue
                try:
                    target=created+timedelta(hours=hours)
                    client=create_trade_market_client()
                    price=historical_price_at(client,row["symbol"],target,now=now)
                    if price is None:
                        raise RuntimeError("historical price unavailable")
                    ret=(float(price)-float(row["entry_price"]))/float(row["entry_price"])*100
                    conn.execute("INSERT INTO outcomes VALUES(?,?,?,?,?)",(row["project_key"],horizon,target.isoformat(),price,ret))
                    updated += 1
                except Exception as exc:
                    errors.append(f"{row['symbol']} {horizon}: {exc}")
    return {"updated": updated, "errors": errors[:10]}


def get_learning_stats():
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) predictions, (SELECT COUNT(*) FROM outcomes) outcomes FROM predictions").fetchone()
    return dict(row)
