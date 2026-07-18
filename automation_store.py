import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path('data') / 'automation_state.db'


def utc_iso():
    return datetime.now(timezone.utc).isoformat()


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('''
        CREATE TABLE IF NOT EXISTS service_state (
            service TEXT PRIMARY KEY,
            last_run TEXT,
            last_success TEXT,
            last_error TEXT,
            payload_json TEXT,
            updated_at TEXT NOT NULL
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS notification_keys (
            key TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            created_at TEXT NOT NULL,
            payload_json TEXT
        )
    ''')
    return conn


def save_service_state(service, success, payload=None, error=None):
    now = utc_iso()
    with connect() as conn:
        previous = conn.execute(
            'SELECT last_success FROM service_state WHERE service = ?',
            (service,),
        ).fetchone()
        last_success = now if success else (previous['last_success'] if previous else None)
        conn.execute('''
            INSERT INTO service_state(service,last_run,last_success,last_error,payload_json,updated_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(service) DO UPDATE SET
                last_run=excluded.last_run,
                last_success=excluded.last_success,
                last_error=excluded.last_error,
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
        ''', (
            service, now, last_success, None if success else str(error)[:1500],
            json.dumps(payload or {}, ensure_ascii=False), now,
        ))


def get_service_states():
    with connect() as conn:
        rows = conn.execute('SELECT * FROM service_state ORDER BY service').fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item['payload'] = json.loads(item.get('payload_json') or '{}')
        except Exception:
            item['payload'] = {}
        result.append(item)
    return result


def claim_notification(key, category, payload=None):
    try:
        with connect() as conn:
            conn.execute(
                'INSERT INTO notification_keys(key,category,created_at,payload_json) VALUES(?,?,?,?)',
                (key, category, utc_iso(), json.dumps(payload or {}, ensure_ascii=False)),
            )
        return True
    except sqlite3.IntegrityError:
        return False
