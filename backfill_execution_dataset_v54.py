"""Reconstruct first-hit execution outcomes for V22 shadow signals.

Uses Binance USDT perpetual 5m history with explicit start/end pagination. Rows that
cannot be proved are stored UNRESOLVED rather than guessed.
"""
from __future__ import annotations
import argparse, json, os, time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
import requests

BASE=os.getenv('BINANCE_FUTURES_API_BASE','https://fapi.binance.com')

def _dt(v):
    if not v:return None
    try:return datetime.fromisoformat(str(v).replace('Z','+00:00')).astimezone(timezone.utc)
    except Exception:return None

def _num(v,d=0.0):
    try:return float(v)
    except Exception:return d

def _side(v):return 'SHORT' if 'SHORT' in str(v or '').upper() else 'LONG'

def _candles(symbol,start,end,session=None):
    s=session or requests.Session(); cursor=int(start.timestamp()*1000); stop=int(end.timestamp()*1000); out=[]
    while cursor<stop:
        r=s.get(BASE+'/fapi/v1/klines',params={'symbol':symbol.upper(),'interval':'5m','startTime':cursor,'endTime':stop,'limit':1500},timeout=float(os.getenv('EXECUTION_BACKFILL_HTTP_TIMEOUT','20'))); r.raise_for_status(); rows=r.json() or []
        if not rows:break
        out.extend(rows); nxt=int(rows[-1][6])+1
        if nxt<=cursor:break
        cursor=nxt
        if len(rows)<1500:break
        time.sleep(float(os.getenv('EXECUTION_BACKFILL_SLEEP','0.08')))
    return out

def _resolve(row,candles):
    side=_side(row.get('direction')); entry=_num(row.get('actual_entry') or row.get('target_entry')); stop=_num(row.get('stop')); tp1=_num(row.get('tp1')); filled=_dt(row.get('filled_at'))
    if not (entry>0 and filled and stop>0):return {'outcome':'UNRESOLVED'}
    best=0.0; worst=0.0; bars=0; exit_price=None; reason=None; exit_at=None
    for c in candles:
        try:high=float(c[2]); low=float(c[3]); close=float(c[4]); end=datetime.fromtimestamp(float(c[6])/1000,tz=timezone.utc)
        except Exception:continue
        if end<=filled:continue
        bars+=1
        fav=(high-entry)/entry*100 if side=='LONG' else (entry-low)/entry*100; adv=(low-entry)/entry*100 if side=='LONG' else (entry-high)/entry*100
        best=max(best,fav); worst=min(worst,adv)
        sl_hit=low<=stop if side=='LONG' else high>=stop
        # Match the live Paper engine exactly: the simulated position is closed at TP1.
        hit_tp=(1,tp1) if tp1>0 and ((high>=tp1) if side=='LONG' else (low<=tp1)) else None
        # Ambiguous same-candle first hit is conservatively treated as SL.
        if sl_hit:reason='SL'; exit_price=stop; exit_at=end; break
        if hit_tp:reason=f'TP{hit_tp[0]}'; exit_price=hit_tp[1]; exit_at=end; break
    if reason is None and candles:
        c=candles[-1]; exit_price=float(c[4]); exit_at=datetime.fromtimestamp(float(c[6])/1000,tz=timezone.utc); reason='TIME_EXIT'
    if exit_price is None:return {'outcome':'UNRESOLVED'}
    gross=((exit_price-entry)/entry*100) if side=='LONG' else ((entry-exit_price)/entry*100)
    fees=2*float(os.getenv('PAPER_FEE_PCT_PER_SIDE','0.06')); slip=2*float(os.getenv('PAPER_SLIPPAGE_PCT','0.03')); net=gross-fees-slip
    risk=abs(entry-stop)/entry*100; rmult=net/risk if risk>1e-9 else 0
    return {'outcome':reason,'exit_reason':reason,'exit_at':exit_at.isoformat(),'net_return_pct':net,'r_multiple':rmult,'mfe_pct':best,'mae_pct':worst,'bars_to_exit':bars}

def _client():
    from cloud_client import get_supabase_client
    return get_supabase_client()

def _all_shadows(limit):
    out=[]; off=0; page=1000
    while len(out)<limit:
        rows=(_client().table('shadow_signals_v22').select('*').order('created_at',desc=False).range(off,min(off+page-1,limit-1)).execute().data or [])
        if not rows:break
        out.extend(rows)
        if len(rows)<page:break
        off+=len(rows)
    return out[:limit]

def backfill(limit=10000,dry_run=False):
    rows=_all_shadows(limit); done=unresolved=errors=0; sess=requests.Session()
    for row in rows:
        try:
            created=_dt(row.get('created_at')); filled=_dt(row.get('filled_at')); expires=_dt(row.get('expires_at')); status=str(row.get('status') or '').lower(); payload=row.get('payload') or {}
            base={'shadow_id':row['id'],'fingerprint':payload.get('fingerprint') if isinstance(payload,dict) else None,'symbol':row.get('symbol'),'direction':row.get('direction'),'setup':row.get('setup'),'source':row.get('source'),'signal_created_at':row.get('created_at'),'target_entry':row.get('target_entry'),'actual_entry':row.get('actual_entry'),'filled_at':row.get('filled_at'),'feature_payload':payload if isinstance(payload,dict) else {},'provider':'binance_futures','candle_interval':'5m','label_version':'first_hit_v54','updated_at':datetime.now(timezone.utc).isoformat()}
            if status=='expired' and not filled:
                base.update(entry_status='expired',outcome='NO_FILL')
            elif status=='entry_unresolved' and not filled:
                base.update(entry_status='unresolved',outcome='UNRESOLVED'); unresolved+=1
            elif filled:
                end=min(filled+timedelta(hours=float(os.getenv('EXECUTION_BACKFILL_MAX_HOLD_HOURS','72'))),datetime.now(timezone.utc)); candles=_candles(str(row.get('symbol')),filled,end,sess); res=_resolve(row,candles); base.update(entry_status='filled',**res)
                if res.get('outcome')=='UNRESOLVED':unresolved+=1
            else:
                base.update(entry_status='unresolved',outcome='UNRESOLVED'); unresolved+=1
            if filled and created:base['fill_delay_minutes']=(filled-created).total_seconds()/60
            if not dry_run:_client().table('execution_training_dataset_v54').upsert(base,on_conflict='shadow_id').execute()
            done+=1
        except Exception as exc:
            errors+=1; print('ERROR',row.get('symbol'),row.get('id'),type(exc).__name__,exc)
    return {'status':'ok','rows':len(rows),'written':done,'unresolved':unresolved,'errors':errors,'dry_run':dry_run}

def main():
    p=argparse.ArgumentParser(); p.add_argument('--limit',type=int,default=int(os.getenv('EXECUTION_BACKFILL_MAX_ROWS','10000'))); p.add_argument('--dry-run',action='store_true'); a=p.parse_args(); print(json.dumps(backfill(a.limit,a.dry_run),ensure_ascii=False))
if __name__=='__main__':main()
