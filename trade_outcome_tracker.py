import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from binance_client import BinanceFuturesClient
from config import BASE_URL, FUTURES_DATA_URL

DB_PATH = Path('data') / 'trade_outcomes.db'
HORIZONS = {'1h': 1, '24h': 24, '7d': 24 * 7}


def utc_now():
    return datetime.now(timezone.utc)


def utc_iso():
    return utc_now().isoformat()


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_trade_outcomes():
    with get_connection() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS tracked_signals (
                fingerprint TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                direction TEXT,
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
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS trade_outcomes (
                fingerprint TEXT NOT NULL,
                horizon TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                price REAL,
                return_percent REAL,
                result_label TEXT,
                PRIMARY KEY(fingerprint, horizon)
            )
        ''')

    with get_connection() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tracked_signals)").fetchall()}
        if "ai_score" not in columns:
            conn.execute("ALTER TABLE tracked_signals ADD COLUMN ai_score REAL")
        if "ai_tier" not in columns:
            conn.execute("ALTER TABLE tracked_signals ADD COLUMN ai_tier TEXT")


def _entry_from_signal(signal):
    value = signal.get('entryPrice')
    if value is not None:
        return float(value)
    text = str(signal.get('entryText') or '').replace('>', '').strip()
    if '–' in text:
        parts = text.split('–', 1)
        try:
            return (float(parts[0]) + float(parts[1])) / 2
        except Exception:
            return None
    try:
        return float(text)
    except Exception:
        return None


def register_trade_signal(signal):
    initialize_trade_outcomes()
    entry = _entry_from_signal(signal)
    if entry is None:
        return False
    with get_connection() as conn:
        conn.execute('''
            INSERT INTO tracked_signals (
                fingerprint, symbol, direction, entry_price, stop, tp1, tp2, tp3,
                score, probability, ai_score, ai_tier, created_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fingerprint) DO NOTHING
        ''', (
            signal['fingerprint'], signal['symbol'], signal.get('direction'), entry,
            signal.get('stop'), signal.get('tp1'), signal.get('tp2'), signal.get('tp3'),
            signal.get('score'), signal.get('probability'), signal.get('aiScore'), signal.get('aiTier'), utc_iso(),
            json.dumps(signal, ensure_ascii=False),
        ))
    return True


def _label(row, price):
    direction = row['direction']
    stop, tp1, tp2, tp3 = row['stop'], row['tp1'], row['tp2'], row['tp3']
    if direction == 'LONG_BIAS':
        if tp3 and price >= tp3: return 'TP3'
        if tp2 and price >= tp2: return 'TP2'
        if tp1 and price >= tp1: return 'TP1'
        if stop and price <= stop: return 'SL'
    elif direction == 'SHORT_BIAS':
        if tp3 and price <= tp3: return 'TP3'
        if tp2 and price <= tp2: return 'TP2'
        if tp1 and price <= tp1: return 'TP1'
        if stop and price >= stop: return 'SL'
    return 'OPEN'


def update_trade_outcomes():
    initialize_trade_outcomes()
    client = BinanceFuturesClient(BASE_URL, FUTURES_DATA_URL)
    now = utc_now()
    updated, errors = 0, []
    with get_connection() as conn:
        rows = conn.execute('SELECT * FROM tracked_signals').fetchall()
        for row in rows:
            created = datetime.fromisoformat(row['created_at'].replace('Z', '+00:00'))
            for horizon, hours in HORIZONS.items():
                if now < created + timedelta(hours=hours):
                    continue
                if conn.execute('SELECT 1 FROM trade_outcomes WHERE fingerprint=? AND horizon=?', (row['fingerprint'], horizon)).fetchone():
                    continue
                try:
                    ticker = client.ticker_24h(row['symbol'])
                    price = float(ticker.get('lastPrice') or 0)
                    entry = float(row['entry_price'])
                    raw_ret = (price - entry) / entry * 100
                    signed_ret = raw_ret if row['direction'] == 'LONG_BIAS' else -raw_ret
                    conn.execute('''
                        INSERT INTO trade_outcomes VALUES (?, ?, ?, ?, ?, ?)
                    ''', (row['fingerprint'], horizon, now.isoformat(), price, signed_ret, _label(row, price)))
                    updated += 1
                except Exception as exc:
                    errors.append(f"{row['symbol']} {horizon}: {exc}")
    return {'updated': updated, 'errors': errors[:10]}


def get_trade_performance():
    initialize_trade_outcomes()
    with get_connection() as conn:
        rows = conn.execute('''
            SELECT horizon, COUNT(*) count,
                   AVG(return_percent) avg_return,
                   SUM(CASE WHEN return_percent > 0 THEN 1 ELSE 0 END) wins,
                   SUM(CASE WHEN result_label LIKE 'TP%' THEN 1 ELSE 0 END) tp_hits,
                   SUM(CASE WHEN result_label = 'SL' THEN 1 ELSE 0 END) sl_hits
            FROM trade_outcomes GROUP BY horizon ORDER BY CASE horizon WHEN '1h' THEN 1 WHEN '24h' THEN 2 ELSE 3 END
        ''').fetchall()
    return [dict(row) for row in rows]
