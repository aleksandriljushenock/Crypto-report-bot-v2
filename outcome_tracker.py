import json
import sqlite3
from core.sqlite_utils import connect as safe_sqlite_connect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests


DATABASE_PATH = Path("data") / "alpha_outcomes.db"
COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
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
    with _connect() as conn:
        conn.execute(
            """INSERT INTO predictions(project_key,coin_id,symbol,score,components_json,created_at,entry_price)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(project_key) DO UPDATE SET
            coin_id=COALESCE(excluded.coin_id,predictions.coin_id), symbol=excluded.symbol,
            score=excluded.score, components_json=excluded.components_json,
            entry_price=COALESCE(predictions.entry_price,excluded.entry_price)""",
            (project_key, coin_id, symbol, score, json.dumps(components, ensure_ascii=False),
             datetime.now(timezone.utc).isoformat(), entry_price),
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
                    response = requests.get(COINGECKO_URL, params={"ids": row["coin_id"], "vs_currencies": "usd"}, timeout=(6, timeout))
                    response.raise_for_status()
                    price = response.json().get(row["coin_id"], {}).get("usd")
                    if price is None:
                        continue
                    ret = (float(price) - float(row["entry_price"])) / float(row["entry_price"]) * 100
                    conn.execute("INSERT INTO outcomes VALUES(?,?,?,?,?)", (row["project_key"], horizon, now.isoformat(), price, ret))
                    updated += 1
                except Exception as exc:
                    errors.append(f"{row['symbol']} {horizon}: {exc}")
    return {"updated": updated, "errors": errors[:10]}


def get_learning_stats():
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) predictions, (SELECT COUNT(*) FROM outcomes) outcomes FROM predictions").fetchone()
    return dict(row)
