import json
import sqlite3
from core.sqlite_utils import connect as safe_sqlite_connect
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trade_market_client import create_trade_market_client, normalize_trade_symbol
from market_errors import UnsupportedSymbolError
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
    conn.execute(
        """CREATE TABLE IF NOT EXISTS outcome_failures (
        project_key TEXT, horizon TEXT, reason TEXT, observed_at TEXT,
        PRIMARY KEY(project_key,horizon))"""
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
    market_unavailable = []
    client=create_trade_market_client()
    with _connect() as conn:
        predictions = conn.execute("SELECT * FROM predictions WHERE coin_id IS NOT NULL AND entry_price IS NOT NULL").fetchall()
        for row in predictions:
            created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            symbol=normalize_trade_symbol(row["symbol"])
            terminal_symbol=False
            for horizon, hours in HORIZONS.items():
                if terminal_symbol or now < created + timedelta(hours=hours):
                    continue
                exists = conn.execute("SELECT 1 FROM outcomes WHERE project_key=? AND horizon=?",(row["project_key"], horizon)).fetchone()
                failed = conn.execute("SELECT 1 FROM outcome_failures WHERE project_key=? AND horizon=?",(row["project_key"], horizon)).fetchone()
                if exists or failed:
                    continue
                try:
                    target=created+timedelta(hours=hours)
                    price=historical_price_at(client,symbol,target,now=now)
                    if price is None:
                        raise RuntimeError("historical price unavailable")
                    ret=(float(price)-float(row["entry_price"]))/float(row["entry_price"])*100
                    conn.execute("INSERT INTO outcomes VALUES(?,?,?,?,?)",(row["project_key"],horizon,target.isoformat(),price,ret))
                    updated += 1
                except UnsupportedSymbolError as exc:
                    reason=f'market-unavailable:{symbol}:{exc}'
                    market_unavailable.append(f'{symbol} {horizon}: {exc}')
                    # Unsupported/delisted market is terminal for all due/future horizons of this prediction.
                    for h2 in HORIZONS:
                        conn.execute("INSERT OR IGNORE INTO outcome_failures VALUES(?,?,?,?)",(row['project_key'],h2,reason,now.isoformat()))
                    terminal_symbol=True
                except Exception as exc:
                    errors.append(f"{symbol} {horizon}: {exc}")
    return {"updated": updated, "errors": errors[:10], "market_unavailable": market_unavailable[:50]}

def get_learning_stats():
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) predictions, (SELECT COUNT(*) FROM outcomes) outcomes FROM predictions").fetchone()
    return dict(row)
