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
import logging
from core.sqlite_utils import connect as safe_sqlite_connect
from datetime import datetime, timedelta, timezone
from pathlib import Path

from analyzer import parse_klines
from trade_market_client import create_trade_market_client
from historical_prices import historical_price_at, historical_candles_between

DB_PATH = Path('data') / 'shadow_signals.db'
HORIZONS = (6, 12, 24)
_RESTORED = False
log=logging.getLogger('shadow_signals')

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
        log.exception('Shadow cloud upsert failed: %s', row.get('id'))


def _cloud_update_signal(sid, values):
    if not _cloud_enabled(): return
    try:
        _cloud().table('shadow_signals_v22').update(values).eq('id', sid).execute()
    except Exception:
        log.exception('Shadow cloud update failed: %s', sid)

def _cloud_insert_outcome(row):
    if not _cloud_enabled(): return
    try:
        _cloud().table('shadow_outcomes_v22').upsert(row, on_conflict='shadow_id,horizon_hours').execute()
    except Exception:
        log.exception('Shadow cloud outcome upsert failed: %s', row.get('shadow_id'))


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
            rows=[]; start=0; page=max(50, integer('SHADOW_RECOVERY_PAGE_SIZE', 500)); cap=max(page, integer('SHADOW_RECOVERY_MAX_ROWS', 10000))
            while len(rows)<cap:
                chunk=(_cloud().table('shadow_signals_v22').select('*').in_('status',['pending_entry','entry_unresolved','filled']).order('created_at', desc=False).range(start,min(start+page-1,cap-1)).execute().data or [])
                rows.extend(chunk)
                if len(chunk)<page: break
                start += len(chunk)
            with _conn() as c:
                for r in rows:
                    c.execute('''INSERT OR REPLACE INTO shadow_signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
                        r.get('id'),r.get('symbol'),r.get('direction'),r.get('setup'),r.get('reason'),r.get('source'),r.get('created_at'),r.get('expires_at'),r.get('status'),r.get('target_entry'),r.get('actual_entry'),r.get('filled_at'),r.get('stop'),r.get('tp1'),r.get('tp2'),r.get('tp3'),r.get('score'),r.get('probability'),r.get('quality'),r.get('ev'),json.dumps(r.get('payload') or {},ensure_ascii=False),r.get('updated_at') or _iso()))
        except Exception:
            log.exception('Shadow cloud recovery failed')

def _side(direction):
    d=str(direction or "").upper()
    if d in {"LONG","LONG_BIAS","BUY"}: return "LONG"
    if d in {"SHORT","SHORT_BIAS","SELL"}: return "SHORT"
    return None

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
            structural_key=str(item.get('fingerprint') or '')
            explicit=item.get('eventFingerprint') or item.get('event_id') or item.get('signal_created_at') or item.get('created_at') or item.get('generated_at')
            bucket_seconds=max(60,int(number('SHADOW_EVENT_BUCKET_SECONDS',900)))
            event_key=str(explicit) if explicit else str(int(now.timestamp())//bucket_seconds)
            raw='|'.join([symbol,str(item.get('direction') or ''),str(item.get('setup') or ''),f'{entry:.8f}',reason,structural_key,event_key])
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
    side=_side(row['direction'])
    if side is None: return 'INVALID_DIRECTION'
    stop=float(row['stop'] or 0); tp1=float(row['tp1'] or 0); tp2=float(row['tp2'] or 0); tp3=float(row['tp3'] or 0)
    if side=='LONG':
        if tp3 and price>=tp3:return 'HORIZON_AT_OR_BEYOND_TP3'
        if tp2 and price>=tp2:return 'HORIZON_AT_OR_BEYOND_TP2'
        if tp1 and price>=tp1:return 'HORIZON_AT_OR_BEYOND_TP1'
        if stop and price<=stop:return 'HORIZON_AT_OR_BEYOND_SL'
    else:
        if tp3 and price<=tp3:return 'HORIZON_AT_OR_BEYOND_TP3'
        if tp2 and price<=tp2:return 'HORIZON_AT_OR_BEYOND_TP2'
        if tp1 and price<=tp1:return 'HORIZON_AT_OR_BEYOND_TP1'
        if stop and price>=stop:return 'HORIZON_AT_OR_BEYOND_SL'
    return 'OPEN'

def update_shadow_signals():
    initialize(); client=create_trade_market_client(); now=_now(); updated=0
    with _conn() as c:
        rows=c.execute("SELECT * FROM shadow_signals WHERE status IN ('pending_entry','entry_unresolved','filled')").fetchall()
        for row in rows:
            created=_dt(row['created_at']); expires=_dt(row['expires_at'])
            if not created: continue
            candles=[]; history_start=None; history_end=None; interval_minutes=5
            try:
                window_end=expires or now
                hist, _iv, interval_minutes, history_start, history_end = historical_candles_between(
                    client,row['symbol'],created,window_end,now=now,max_bars=max(300,integer('SHADOW_HISTORY_MAX_BARS',1000))
                )
                candles=[{'open_time':c[0].timestamp()*1000,'close':c[2],'high':c[3],'low':c[4],'_end':c[1]} for c in hist]
            except Exception:
                candles=[]
            if row['status'] in ('pending_entry','entry_unresolved'):
                target=float(row['target_entry'] or 0); side=_side(row['direction'])
                if side is None:
                    c.execute("UPDATE shadow_signals SET status='invalid', updated_at=? WHERE id=?",(_iso(now),row['id']))
                    continue
                setup=str(row['setup'] or '').upper(); fill=None; fill_dt=None; boundary_uncertain=False; execution_precision=None
                for candle in candles:
                    try:
                        cdt=datetime.fromtimestamp(float(candle['open_time'])/1000,tz=timezone.utc)
                        cend=candle.get('_end') or (cdt+timedelta(minutes=interval_minutes))
                    except Exception: continue
                    if cend > now:
                        continue
                    if setup=='BREAKOUT':
                        touched = candle['high'] >= target if side=='LONG' else candle['low'] <= target
                    else:
                        touched = candle['low'] <= target <= candle['high']
                    # V49: an OHLC candle crossing created/expires cannot prove whether a
                    # touch happened inside the legal slice. Keep it unresolved if it could.
                    boundary = cdt < created or bool(expires and cend > expires)
                    if boundary:
                        if touched:
                            boundary_uncertain=True
                            # Strict chronological uncertainty: do not accept a later
                            # definite fill while an earlier boundary may already contain
                            # the first legal fill.
                            break
                        continue
                    if touched:
                        fill=target; fill_dt=cend; execution_precision=f'{interval_minutes}m_ohlc'; break
                if fill:
                    c.execute("UPDATE shadow_signals SET status='filled', actual_entry=?, filled_at=?, updated_at=? WHERE id=?",(fill,_iso(fill_dt),_iso(now),row['id']))
                    cloud_payload=json.loads(row['payload_json'] or '{}'); cloud_payload['execution_precision']=execution_precision
                    _cloud_upsert_signal({'id':row['id'],'symbol':row['symbol'],'direction':row['direction'],'setup':row['setup'],'reason':row['reason'],'source':row['source'],'created_at':row['created_at'],'expires_at':row['expires_at'],'status':'filled','target_entry':target,'actual_entry':fill,'filled_at':_iso(fill_dt),'stop':row['stop'],'tp1':row['tp1'],'tp2':row['tp2'],'tp3':row['tp3'],'score':row['score'],'probability':row['probability'],'quality':row['quality'],'ev':row['ev'],'payload':cloud_payload,'updated_at':_iso(now)})
                    updated+=1
                    row=dict(row); row['status']='filled'; row['actual_entry']=fill; row['filled_at']=_iso(fill_dt)
                elif expires and now>=expires and history_end and history_end >= expires and not boundary_uncertain:
                    c.execute("UPDATE shadow_signals SET status='expired', updated_at=? WHERE id=?",(_iso(now),row['id'])); _cloud_update_signal(row['id'], {'status':'expired','updated_at':_iso(now)}); updated+=1; continue
                elif expires and now>=expires and boundary_uncertain:
                    c.execute("UPDATE shadow_signals SET status='entry_unresolved', updated_at=? WHERE id=?",(_iso(now),row['id'])); _cloud_update_signal(row['id'], {'status':'entry_unresolved','updated_at':_iso(now)}); updated+=1; continue
                elif expires and now>=expires and (not history_end or history_end < expires):
                    # Missing or partial history cannot prove that entry was never touched.
                    c.execute("UPDATE shadow_signals SET status='entry_unresolved', updated_at=? WHERE id=?",(_iso(now),row['id'])); _cloud_update_signal(row['id'], {'status':'entry_unresolved','updated_at':_iso(now)}); updated+=1; continue
            if row['status']=='filled':
                filled=_dt(row['filled_at']); entry=float(row['actual_entry'] or row['target_entry'] or 0)
                if not filled or not entry: continue
                side=_side(row['direction'])
                if side is None:
                    c.execute("UPDATE shadow_signals SET status='invalid', updated_at=? WHERE id=?",(_iso(now),row['id']))
                    continue
                for hours in HORIZONS:
                    target_time=filled+timedelta(hours=hours)
                    if now < target_time: continue
                    if c.execute('SELECT 1 FROM shadow_outcomes WHERE shadow_id=? AND horizon_hours=?',(row['id'],hours)).fetchone(): continue
                    try: price=historical_price_at(client,row['symbol'],target_time,now=now)
                    except Exception: price=None
                    if not price: continue
                    raw_ret=(price-entry)/entry*100; signed=raw_ret if side=='LONG' else -raw_ret
                    label=_label(row,price); c.execute('INSERT INTO shadow_outcomes VALUES (?,?,?,?,?,?)',(row['id'],hours,_iso(target_time),price,signed,label)); _cloud_insert_outcome({'shadow_id':row['id'],'horizon_hours':hours,'observed_at':_iso(target_time),'price':price,'return_pct':signed,'label':label}); updated+=1
                if now >= filled+timedelta(hours=max(HORIZONS)):
                    have=int(c.execute('SELECT COUNT(DISTINCT horizon_hours) n FROM shadow_outcomes WHERE shadow_id=?',(row['id'],)).fetchone()['n'] or 0)
                    if have >= len(HORIZONS):
                        c.execute("UPDATE shadow_signals SET status='observed', updated_at=? WHERE id=?",(_iso(now),row['id'])); _cloud_update_signal(row['id'], {'status':'observed','updated_at':_iso(now)})
    return {'updated':updated}

def summary():
    initialize()
    with _conn() as c:
        counts={r['status']:r['n'] for r in c.execute('SELECT status,COUNT(*) n FROM shadow_signals GROUP BY status').fetchall()}
        outcomes=c.execute('SELECT COUNT(*) n, AVG(return_pct) avg_ret, SUM(CASE WHEN return_pct>0 THEN 1 ELSE 0 END) wins FROM shadow_outcomes WHERE horizon_hours=24').fetchone()
    n=int(outcomes['n'] or 0); wins=int(outcomes['wins'] or 0)
    return {'counts':counts,'outcomes24h':n,'avgReturn24h':float(outcomes['avg_ret'] or 0),'winRate24h':(100*wins/n if n else 0)}
