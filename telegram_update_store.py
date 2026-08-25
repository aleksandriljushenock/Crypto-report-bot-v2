from __future__ import annotations
import os
from pathlib import Path
from core.sqlite_utils import connect as sqlite_connect

DB_PATH=Path(os.getenv("TELEGRAM_UPDATE_DB_PATH","data/telegram_updates_v45.db"))

def _conn():
    DB_PATH.parent.mkdir(parents=True,exist_ok=True)
    c=sqlite_connect(DB_PATH,timeout=30)
    c.execute("CREATE TABLE IF NOT EXISTS processed_updates(update_id INTEGER PRIMARY KEY, processed_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    return c

def processed(update_id:int)->bool:
    with _conn() as c:
        return c.execute("SELECT 1 FROM processed_updates WHERE update_id=?",(int(update_id),)).fetchone() is not None

def mark_processed(update_id:int)->None:
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO processed_updates(update_id) VALUES(?)",(int(update_id),))
        c.execute("DELETE FROM processed_updates WHERE update_id < ?",(max(0,int(update_id)-50000),))

def next_offset()->int|None:
    with _conn() as c:
        r=c.execute("SELECT MAX(update_id) FROM processed_updates").fetchone()
        return (int(r[0])+1) if r and r[0] is not None else None
