"""v22 near-signal watchlist.

Stores candidates that missed one of the final gates so the monitor can re-scan
those symbols more frequently than the full market universe.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path('data') / 'near_signal_watchlist.db'


def _now():
    return datetime.now(timezone.utc)


def _iso(dt=None):
    return (dt or _now()).isoformat()


def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def initialize():
    with _conn() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS near_signals (
            symbol TEXT PRIMARY KEY,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            next_check_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            reason TEXT,
            score REAL,
            probability REAL,
            quality REAL,
            ev REAL,
            source TEXT,
            payload_json TEXT NOT NULL
        )''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_near_due ON near_signals(next_check_at, expires_at)')


def _interesting(item):
    reason = str(item.get('reason') or '')
    allowed = ('Score', 'R/R', 'Probability', 'Quality', 'EV', 'AI gate')
    if not any(x.lower() in reason.lower() for x in allowed):
        return False
    probability = float(item.get('probability') or 0)
    quality = float(item.get('qualityScore') or 0)
    # Keep candidates near enough to a final gate. AI rejects may not have both.
    min_p = float(os.getenv('NEAR_SIGNAL_MIN_PROBABILITY', '58'))
    min_q = float(os.getenv('NEAR_SIGNAL_MIN_QUALITY', '62'))
    return probability >= min_p or quality >= min_q


def upsert_near_candidates(items, source='scan'):
    initialize()
    ttl_hours = max(1.0, float(os.getenv('NEAR_SIGNAL_TTL_HOURS', '12')))
    check_minutes = max(1, int(float(os.getenv('NEAR_SIGNAL_RESCAN_MINUTES', '5'))))
    now = _now()
    count = 0
    with _conn() as conn:
        # Expire stale rows first.
        conn.execute('DELETE FROM near_signals WHERE expires_at < ?', (_iso(now),))
        for item in items or []:
            if not _interesting(item):
                continue
            symbol = str(item.get('symbol') or '').upper()
            if not symbol:
                continue
            row = (
                symbol, _iso(now), _iso(now), _iso(now + timedelta(minutes=check_minutes)),
                _iso(now + timedelta(hours=ttl_hours)), str(item.get('reason') or ''),
                float(item.get('score') or 0), float(item.get('probability') or 0),
                float(item.get('qualityScore') or 0), float(item.get('expectedValuePct') or 0),
                source, json.dumps(item, ensure_ascii=False),
            )
            conn.execute('''INSERT INTO near_signals (
                symbol, first_seen, last_seen, next_check_at, expires_at, reason,
                score, probability, quality, ev, source, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                last_seen=excluded.last_seen,
                next_check_at=excluded.next_check_at,
                expires_at=excluded.expires_at,
                reason=excluded.reason,
                score=excluded.score,
                probability=excluded.probability,
                quality=excluded.quality,
                ev=excluded.ev,
                source=excluded.source,
                payload_json=excluded.payload_json
            ''', row)
            count += 1
    return count


def get_due_symbols(limit=None):
    initialize()
    limit = max(1, int(limit or os.getenv('NEAR_SIGNAL_RESCAN_LIMIT', '24')))
    now = _iso()
    with _conn() as conn:
        conn.execute('DELETE FROM near_signals WHERE expires_at < ?', (now,))
        rows = conn.execute('''SELECT symbol FROM near_signals
            WHERE next_check_at <= ? AND expires_at >= ?
            ORDER BY quality DESC, probability DESC, score DESC, last_seen DESC
            LIMIT ?''', (now, now, limit)).fetchall()
    return [r['symbol'] for r in rows]


def mark_checked(symbols, promoted=None):
    initialize()
    promoted = {str(x).upper() for x in (promoted or [])}
    minutes = max(1, int(float(os.getenv('NEAR_SIGNAL_RESCAN_MINUTES', '5'))))
    next_time = _iso(_now() + timedelta(minutes=minutes))
    with _conn() as conn:
        for symbol in symbols or []:
            symbol = str(symbol).upper()
            if symbol in promoted:
                conn.execute('DELETE FROM near_signals WHERE symbol=?', (symbol,))
            else:
                conn.execute('UPDATE near_signals SET next_check_at=? WHERE symbol=?', (next_time, symbol))


def get_rows(limit=10):
    initialize()
    now = _iso()
    with _conn() as conn:
        conn.execute('DELETE FROM near_signals WHERE expires_at < ?', (now,))
        rows = conn.execute('''SELECT * FROM near_signals ORDER BY quality DESC, probability DESC, score DESC LIMIT ?''', (max(1, int(limit)),)).fetchall()
    return [dict(x) for x in rows]
