import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path('data') / 'trade_signals.db'


def utc_now():
    return datetime.now(timezone.utc)


def utc_iso():
    return utc_now().isoformat()


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_signal_store():
    with get_connection() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS monitor_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER NOT NULL DEFAULT 0,
                interval_minutes INTEGER NOT NULL DEFAULT 15,
                chat_id TEXT,
                updated_at TEXT NOT NULL
            )
        ''')
        conn.execute('''
            INSERT INTO monitor_settings (id, enabled, interval_minutes, updated_at)
            VALUES (1, 0, 15, ?)
            ON CONFLICT(id) DO NOTHING
        ''', (utc_iso(),))
        conn.execute('''
            CREATE TABLE IF NOT EXISTS trade_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT,
                setup TEXT,
                status TEXT,
                score REAL,
                rr REAL,
                entry_text TEXT,
                stop REAL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                sent_at TEXT
            )
        ''')
        conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_trade_signals_fingerprint
            ON trade_signals(fingerprint, created_at DESC)
        ''')
        conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_trade_signals_created
            ON trade_signals(created_at DESC)
        ''')
    _ensure_signal_columns()


def _ensure_signal_columns():
    with get_connection() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(trade_signals)").fetchall()}
        if "ai_score" not in columns:
            conn.execute("ALTER TABLE trade_signals ADD COLUMN ai_score REAL")
        if "ai_tier" not in columns:
            conn.execute("ALTER TABLE trade_signals ADD COLUMN ai_tier TEXT")
        extras = {
            "market_regime": "TEXT", "probability": "REAL", "confidence": "REAL",
            "uncertainty": "REAL", "smart_money_score": "REAL",
            "news_sentiment": "REAL", "real_result_json": "TEXT"
        }
        for name, sql_type in extras.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE trade_signals ADD COLUMN {name} {sql_type}")


def get_monitor_settings():
    initialize_signal_store()
    with get_connection() as conn:
        row = conn.execute('SELECT * FROM monitor_settings WHERE id = 1').fetchone()
    return dict(row)


def set_monitor_settings(enabled=None, interval_minutes=None, chat_id=None):
    current = get_monitor_settings()
    new_enabled = current['enabled'] if enabled is None else int(bool(enabled))
    new_interval = current['interval_minutes'] if interval_minutes is None else max(5, int(interval_minutes))
    new_chat = current.get('chat_id') if chat_id is None else str(chat_id)
    with get_connection() as conn:
        conn.execute('''
            UPDATE monitor_settings
            SET enabled = ?, interval_minutes = ?, chat_id = ?, updated_at = ?
            WHERE id = 1
        ''', (new_enabled, new_interval, new_chat, utc_iso()))
    return get_monitor_settings()


def signal_recently_sent(fingerprint, cooldown_hours=6):
    cutoff = (utc_now() - timedelta(hours=cooldown_hours)).isoformat()
    with get_connection() as conn:
        row = conn.execute('''
            SELECT id FROM trade_signals
            WHERE fingerprint = ? AND sent_at IS NOT NULL AND sent_at >= ?
            LIMIT 1
        ''', (fingerprint, cutoff)).fetchone()
    return row is not None


def save_signal(signal, sent=False):
    initialize_signal_store()
    with get_connection() as conn:
        cursor = conn.execute('''
            INSERT INTO trade_signals (
                fingerprint, symbol, direction, setup, status, score, rr,
                entry_text, stop, payload_json, created_at, sent_at, ai_score, ai_tier
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            signal['fingerprint'], signal['symbol'], signal.get('direction'),
            signal.get('setup'), signal.get('status'), signal.get('score'),
            signal.get('rr'), signal.get('entryText'), signal.get('stop'),
            json.dumps(signal, ensure_ascii=False), utc_iso(), utc_iso() if sent else None,
            signal.get('aiScore'), signal.get('aiTier'),
        ))
        signal_id = cursor.lastrowid
        conn.execute("""UPDATE trade_signals SET market_regime=?, probability=?, confidence=?, uncertainty=?,
                     smart_money_score=?, news_sentiment=? WHERE id=?""", (
            signal.get('marketRegime'), signal.get('probability'), signal.get('confidence'),
            signal.get('uncertainty'), signal.get('smartMoneyScore'), signal.get('newsSentiment'), signal_id))
        try:
            from learning_max2 import save_observation
            save_observation(signal)
        except Exception:
            pass
        return signal_id


def mark_signal_sent(signal_id):
    with get_connection() as conn:
        conn.execute('UPDATE trade_signals SET sent_at = ? WHERE id = ?', (utc_iso(), signal_id))


def get_recent_signals(limit=10):
    initialize_signal_store()
    with get_connection() as conn:
        rows = conn.execute('''
            SELECT * FROM trade_signals
            ORDER BY created_at DESC
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
