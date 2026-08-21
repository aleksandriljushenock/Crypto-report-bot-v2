import json
import sqlite3
from core.sqlite_utils import connect as safe_sqlite_connect
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path('data') / 'trade_watchlist.db'


def utc_iso():
    return datetime.now(timezone.utc).isoformat()


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = safe_sqlite_connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_watchlist():
    with get_connection() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS watchlist (
                symbol TEXT PRIMARY KEY,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                last_score REAL,
                last_probability REAL,
                best_score REAL,
                best_probability REAL,
                status TEXT,
                direction TEXT,
                source TEXT,
                payload_json TEXT NOT NULL
            )
        ''')
        conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_watchlist_rank
            ON watchlist(best_score DESC, best_probability DESC, last_seen DESC)
        ''')


def upsert_watch_candidate(signal, source='trade_scan'):
    initialize_watchlist()
    symbol = signal.get('symbol')
    if not symbol:
        return
    score = float(signal.get('score') or 0)
    probability = float(signal.get('probability') or 0)
    now = utc_iso()
    with get_connection() as conn:
        conn.execute('''
            INSERT INTO watchlist (
                symbol, first_seen, last_seen, last_score, last_probability,
                best_score, best_probability, status, direction, source, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                last_seen=excluded.last_seen,
                last_score=excluded.last_score,
                last_probability=excluded.last_probability,
                best_score=MAX(watchlist.best_score, excluded.best_score),
                best_probability=MAX(watchlist.best_probability, excluded.best_probability),
                status=excluded.status,
                direction=excluded.direction,
                source=excluded.source,
                payload_json=excluded.payload_json
        ''', (
            symbol, now, now, score, probability, score, probability,
            signal.get('status'), signal.get('direction'), source,
            json.dumps(signal, ensure_ascii=False),
        ))


def get_watchlist(limit=10):
    initialize_watchlist()
    with get_connection() as conn:
        rows = conn.execute('''
            SELECT * FROM watchlist
            ORDER BY last_score DESC, last_probability DESC, last_seen DESC
            LIMIT ?
        ''', (limit,)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item['payload'] = json.loads(item['payload_json'])
        except Exception:
            item['payload'] = {}
        result.append(item)
    return result
