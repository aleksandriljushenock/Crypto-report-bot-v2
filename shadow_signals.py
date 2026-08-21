"""v22 shadow outcomes for rejected/near-final signals.

Shadow signals never reach Telegram as trades and never touch the paper account.
They are tracked to learn whether a gate is too strict. Entry must actually be
observed in 5m candles before return/outcome measurements are valid.
"""
from __future__ import annotations

from core.runtime_config import boolean, integer, number, string

import hashlib
import json
import sqlite3
from core.sqlite_utils import connect as safe_sqlite_connect
from datetime import datetime, timedelta, timezone
from pathlib import Path

from analyzer import parse_klines
from trade_market_client import create_trade_market_client

DB_PATH = Path('data') / 'shadow_signals.db'
HORIZONS = (6, 12, 24)
_RESTORED = False

def _cloud_enabled():
    return boolean('SHADOW_CLOUD_ENABLED', True) and bool(string('SUPABASE_URL', '', strategy=False)) and bool(string('SUPABASE_SERVICE_KEY', '', strategy=False))

def _cloud():
    from cloud_client import get_supabase_client
    return get_supabase_client()

def _cloud_upsert_signal(row):
    if not _cloud_enabled(): return
    try:
        _cloud().table('shadow_signals_v22').upsert(row, on_conflict='id').execute()
    except Exception:
        pass


def _cloud_update_signal(sid, values):
    if not _cloud_enabled(): return
    try:
        _cloud().table('shadow_signals_v22').update(values).eq('id', sid).execute()
    except Exception:
        pass

def _cloud_insert_outcome(row):
    if not _cloud_enabled(): return
    try:
        _cloud().table('shadow_outcomes_v22').upsert(row, on_conflict='shadow_id,horizon_hours').execute()
    except Exception:
        pass


def _now(): return datetime.now(timezone.utc)
def _iso(dt=None): return (dt or _now()).isoformat()

def _dt(v):
    try: return datetime.fromisoformat(str(v).replace('Z', '+00:00'))
    except Exception: return None

def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c=safe_sqlite_connect(DB_PATH, timeout=30); c.row_factory=sqlite3.Row; return c

def initialize():
    with _conn() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS shadow_signals (
            id TEXT PRIMARY KEY, symbol TEXT NOT NULL, direction TEXT, setup TEXT,
            reason TEXT, source TEXT, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
            status TEXT NOT NULL, target_entry REAL, actual_entry REAL, filled_at TEXT,
            stop REAL, tp1 REAL, tp2 REAL, tp3 REAL, score REAL, probability REAL,
            quality REAL, ev REAL, payload_json TEXT NOT NULL, updated_at TEXT NOT NULL
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS shadow_outcomes (
            shadow_id TEXT NOT NULL, horizon_hours INTEGER NOT NULL, observed_at TEXT NOT NULL,
            price REAL, return_pct REAL, label TEXT, PRIMARY KEY(shadow_id,horizon_hours)
        )''')
    global _RESTORED
    if not _RESTORED and _cloud_enabled():
        _RESTORED = True
        try:
            rows = (_cloud().table('shadow_signals_v22').select('*').in_('status',['pending_entry','filled']).limit(200).execute().data or [])
            with _conn() as c:
                for r in rows:
                    c.execute('''INSERT OR REPLACE INTO shadow_signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
                        r.get('id'),r.get('symbol'),r.get('direction'),r.get('setup'),r.get('reason'),r.get('source'),r.get('created_at'),r.get('expires_at'),r.get('status'),r.get('target_entry'),r.get('actual_entry'),r.get('filled_at'),r.get('stop'),r.get('tp1'),r.get('tp2'),r.get('tp3'),r.get('score'),r.get('probability'),r.get('quality'),r.get('ev'),json.dumps(r.get('payload') or {},ensure_ascii=False),r.get('updated_at') or _iso()))
        except Exception:
            pass

def _entry(item):
    try:
        v=float(item.get('entryPrice') or 0)
        if v>0:return v
    except Exception: pass
    text=str(item.get('entryText') or '').replace('>','').strip()
    if '–' in text:
        try:
            a,b=text.split('–',1); return (float(a)+float(b))/2
        except Exception:return None
    try:return float(text)
    except Exception:return None

def register_shadow_candidates(items, source='scan'):
    if not boolean('SHADOW_SIGNALS_ENABLED', True): return 0
    initialize(); now=_now(); ttl=max(6, number('SHADOW_SIGNAL_TTL_HOURS', 24.0)); count=0
    with _conn() as c:
        for item in items or []:
            symbol=str(item.get('symbol') or '').upper(); entry=_entry(item)
            if not symbol or not entry: continue
            reason=str(item.get('reason') or 'filter')
            raw='|'.join([symbol,str(item.get('direction') or ''),str(item.get('setup') or ''),f'{entry:.8f}',reason])
            sid=hashlib.sha256(raw.encode()).hexdigest()
            row=(sid,symbol,item.get('direction'),item.get('setup'),reason,source,_iso(now),_iso(now+timedelta(hours=ttl)),
                 'pending_entry',entry,None,None,item.get('stop'),item.get('tp1'),item.get('tp2'),item.get('tp3'),
                 float(item.get('score') or 0),float(item.get('probability') or 0),float(item.get('qualityScore') or 0),
                 float(item.get('expectedValuePct') or 0),json.dumps(item,ensure_ascii=False),_iso(now))
            before = c.total_changes
            c.execute('''INSERT OR IGNORE INTO shadow_signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',row)
            if c.total_changes > before:
                count+=1
                _cloud_upsert_signal({
                    'id':sid,'symbol':symbol,'direction':item.get('direction'),'setup':item.get('setup'),'reason':reason,'source':source,
                    'created_at':_iso(now),'expires_at':_iso(now+timedelta(hours=ttl)),'status':'pending_entry','target_entry':entry,
                    'actual_entry':None,'filled_at':None,'stop':item.get('stop'),'tp1':item.get('tp1'),'tp2':item.get('tp2'),'tp3':item.get('tp3'),
                    'score':float(item.get('score') or 0),'probability':float(item.get('probability') or 0),'quality':float(item.get('qualityScore') or 0),
                    'ev':float(item.get('expectedValuePct') or 0),'payload':item,'updated_at':_iso(now)})
    return count

def _label(row, price):
    side='SHORT' if 'SHORT' in str(row['direction'] or '') else 'LONG'
    stop=float(row['stop'] or 0); tp1=float(row['tp1'] or 0); tp2=float(row['tp2'] or 0); tp3=float(row['tp3'] or 0)
    if side=='LONG':
        if tp3 and price>=tp3:return 'TP3'
        if tp2 and price>=tp2:return 'TP2'
        if tp1 and price>=tp1:return 'TP1'
        if stop and price<=stop:return 'SL'
    else:
        if tp3 and price<=tp3:return 'TP3'
        if tp2 and price<=tp2:return 'TP2'
        if tp1 and price<=tp1:return 'TP1'
        if stop and price>=stop:return 'SL'
    return 'OPEN'

def update_shadow_signals():
    initialize(); client=create_trade_market_client(); now=_now(); updated=0
    with _conn() as c:
        rows=c.execute("SELECT * FROM shadow_signals WHERE status IN ('pending_entry','filled')").fetchall()
        for row in rows:
            created=_dt(row['created_at']); expires=_dt(row['expires_at'])
            if not created: continue
            try:
                raw=client.klines(row['symbol'],'5m',300) or []
                candles=parse_klines(raw)
            except Exception:
                candles=[]
            if row['status']=='pending_entry':
                target=float(row['target_entry'] or 0); side='SHORT' if 'SHORT' in str(row['direction'] or '') else 'LONG'
                setup=str(row['setup'] or '').upper(); fill=None; fill_dt=None
                for candle in candles:
                    try:
                        cdt=datetime.fromtimestamp(float(candle['open_time'])/1000,tz=timezone.utc)
                    except Exception: continue
                    if cdt < created: continue
                    if setup=='BREAKOUT':
                        touched = candle['high'] >= target if side=='LONG' else candle['low'] <= target
                    else:
                        touched = candle['low'] <= target <= candle['high']
                    if touched:
                        fill=target; fill_dt=cdt; break
                if fill:
                    c.execute("UPDATE shadow_signals SET status='filled', actual_entry=?, filled_at=?, updated_at=? WHERE id=?",(fill,_iso(fill_dt),_iso(now),row['id']))
                    _cloud_upsert_signal({'id':row['id'],'symbol':row['symbol'],'direction':row['direction'],'setup':row['setup'],'reason':row['reason'],'source':row['source'],'created_at':row['created_at'],'expires_at':row['expires_at'],'status':'filled','target_entry':target,'actual_entry':fill,'filled_at':_iso(fill_dt),'stop':row['stop'],'tp1':row['tp1'],'tp2':row['tp2'],'tp3':row['tp3'],'score':row['score'],'probability':row['probability'],'quality':row['quality'],'ev':row['ev'],'payload':json.loads(row['payload_json'] or '{}'),'updated_at':_iso(now)})
                    updated+=1
                    row=dict(row); row['status']='filled'; row['actual_entry']=fill; row['filled_at']=_iso(fill_dt)
                elif expires and now>=expires:
                    c.execute("UPDATE shadow_signals SET status='expired', updated_at=? WHERE id=?",(_iso(now),row['id'])); _cloud_update_signal(row['id'], {'status':'expired','updated_at':_iso(now)}); updated+=1; continue
            if row['status']=='filled':
                filled=_dt(row['filled_at']); entry=float(row['actual_entry'] or row['target_entry'] or 0)
                if not filled or not entry: continue
                price=0.0
                try:
                    t=client.ticker_24h(row['symbol']) or {}; price=float(t.get('markPrice') or t.get('lastPrice') or t.get('price') or 0)
                except Exception: pass
                if not price: continue
                for hours in HORIZONS:
                    if now < filled+timedelta(hours=hours): continue
                    if c.execute('SELECT 1 FROM shadow_outcomes WHERE shadow_id=? AND horizon_hours=?',(row['id'],hours)).fetchone(): continue
                    raw_ret=(price-entry)/entry*100; signed=raw_ret if 'SHORT' not in str(row['direction'] or '') else -raw_ret
                    label=_label(row,price); c.execute('INSERT INTO shadow_outcomes VALUES (?,?,?,?,?,?)',(row['id'],hours,_iso(now),price,signed,label)); _cloud_insert_outcome({'shadow_id':row['id'],'horizon_hours':hours,'observed_at':_iso(now),'price':price,'return_pct':signed,'label':label}); updated+=1
                if now >= filled+timedelta(hours=24):
                    c.execute("UPDATE shadow_signals SET status='observed', updated_at=? WHERE id=?",(_iso(now),row['id'])); _cloud_update_signal(row['id'], {'status':'observed','updated_at':_iso(now)})
    return {'updated':updated}

def summary():
    initialize()
    with _conn() as c:
        counts={r['status']:r['n'] for r in c.execute('SELECT status,COUNT(*) n FROM shadow_signals GROUP BY status').fetchall()}
        outcomes=c.execute('SELECT COUNT(*) n, AVG(return_pct) avg_ret, SUM(CASE WHEN return_pct>0 THEN 1 ELSE 0 END) wins FROM shadow_outcomes WHERE horizon_hours=24').fetchone()
    n=int(outcomes['n'] or 0); wins=int(outcomes['wins'] or 0)
    return {'counts':counts,'outcomes24h':n,'avgReturn24h':float(outcomes['avg_ret'] or 0),'winRate24h':(100*wins/n if n else 0)}
