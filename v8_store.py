from core.sqlite_utils import connect as safe_sqlite_connect
import json, os, sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv('V8_DB_PATH', BASE_DIR / 'data' / 'v8_professional.db'))

@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = safe_sqlite_connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def now_iso(): return datetime.now(timezone.utc).isoformat()

def initialize():
    with connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY, kind TEXT, symbol TEXT, score REAL, payload TEXT, created_at TEXT);
        CREATE INDEX IF NOT EXISTS idx_snapshots_kind_time ON snapshots(kind, created_at DESC);
        CREATE TABLE IF NOT EXISTS portfolio(symbol TEXT PRIMARY KEY, quantity REAL NOT NULL, avg_price REAL NOT NULL DEFAULT 0, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS news_seen(fingerprint TEXT PRIMARY KEY, created_at TEXT);
        CREATE TABLE IF NOT EXISTS feature_stats(feature TEXT PRIMARY KEY, samples INTEGER, wins INTEGER, avg_return REAL, weight REAL, updated_at TEXT);
        ''')

def save_snapshot(kind, payload, symbol='', score=0):
    initialize()
    with connect() as c:
        c.execute('INSERT INTO snapshots(kind,symbol,score,payload,created_at) VALUES(?,?,?,?,?)', (kind,symbol,float(score or 0),json.dumps(payload,ensure_ascii=False,default=str),now_iso()))

def latest(kind, limit=50):
    initialize()
    with connect() as c:
        rows=c.execute('SELECT * FROM snapshots WHERE kind=? ORDER BY id DESC LIMIT ?', (kind,int(limit))).fetchall()
    out=[]
    for r in rows:
        d=dict(r)
        try:d['payload']=json.loads(d['payload'])
        except Exception:pass
        out.append(d)
    return out
