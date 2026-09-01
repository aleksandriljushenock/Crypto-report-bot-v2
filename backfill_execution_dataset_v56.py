"""V56 provider-aware first-hit replay and Paper/Shadow dataset unification.

Shadow rows are replayed chronologically. If TP and SL are touched inside one 5m bar,
the tool attempts a 1m refinement; still-ambiguous rows are explicitly marked and are
excluded from outcome training rather than forced to SL. Paper positions are copied
with their complete signal payload and receive higher sample weight.
"""
from __future__ import annotations
import argparse, json, os, time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple
import requests

PROVIDERS=[x.strip().lower() for x in os.getenv('EXECUTION_BACKFILL_PROVIDERS','binance,bybit,okx,bitget,gate,mexc').split(',') if x.strip()]

def _dt(v):
    if not v:return None
    try:return datetime.fromisoformat(str(v).replace('Z','+00:00')).astimezone(timezone.utc)
    except Exception:return None

def _num(v,d=0.0):
    try:return float(v)
    except Exception:return d

def _side(v):return 'SHORT' if 'SHORT' in str(v or '').upper() else 'LONG'

def _request_json(s,url,params):
    retries=max(1,int(os.getenv('EXECUTION_BACKFILL_HTTP_RETRIES','4')))
    backoff=max(0.05,float(os.getenv('EXECUTION_BACKFILL_HTTP_BACKOFF','0.5')))
    last=None
    for attempt in range(retries):
        try:
            r=s.get(url,params=params,timeout=float(os.getenv('EXECUTION_BACKFILL_HTTP_TIMEOUT','20')))
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last=exc
            if attempt+1<retries: time.sleep(backoff*(2**attempt))
    raise last or RuntimeError('request failed')

def _normalize(provider,data,interval='5m')->List[Tuple[int,float,float,float,int]]:
    duration_ms=60_000 if interval=='1m' else 300_000
    out=[]
    try:
        if provider=='binance':
            rows=data or []
            for c in rows:out.append((int(c[0]),float(c[2]),float(c[3]),float(c[4]),int(c[6])))
        elif provider=='bybit':
            rows=((data or {}).get('result') or {}).get('list') or []
            for c in rows:
                o=int(c[0]); out.append((o,float(c[2]),float(c[3]),float(c[4]),o+duration_ms))
        elif provider=='okx':
            rows=(data or {}).get('data') or []
            for c in rows:
                o=int(c[0]); out.append((o,float(c[2]),float(c[3]),float(c[4]),o+duration_ms))
        elif provider=='bitget':
            rows=(data or {}).get('data') or []
            for c in rows:
                o=int(c[0]); out.append((o,float(c[2]),float(c[3]),float(c[4]),o+duration_ms))
        elif provider=='gate':
            rows=data or []
            for c in rows:
                # Gate futures: [timestamp, volume, close, high, low, open, ...]
                o=int(float(c[0])*1000); out.append((o,float(c[3]),float(c[4]),float(c[2]),o+duration_ms))
        elif provider=='mexc':
            d=(data or {}).get('data') or {}; times=d.get('time') or []
            for i,t in enumerate(times):
                o=int(float(t)*1000); out.append((o,float(d['high'][i]),float(d['low'][i]),float(d['close'][i]),o+duration_ms))
    except Exception:return []
    return sorted(out,key=lambda x:x[0])

def _fetch(provider,symbol,start,end,interval='5m',session=None):
    """Fetch a complete interval, chunking provider limits. Returns (rows, error)."""
    s=session or requests.Session()
    minutes=1 if interval=='1m' else 5
    limits={'binance':1500,'bybit':1000,'okx':300,'bitget':1000,'gate':1000,'mexc':1000}
    limit=limits.get(provider,1000)
    chunk=timedelta(minutes=minutes*limit)
    cursor=start; out=[]
    try:
        while cursor < end:
            stop=min(end,cursor+chunk)
            a=int(cursor.timestamp()*1000); b=int(stop.timestamp()*1000)
            if provider=='binance':
                data=_request_json(s,os.getenv('BINANCE_FUTURES_API_BASE','https://fapi.binance.com')+'/fapi/v1/klines',{'symbol':symbol,'interval':interval,'startTime':a,'endTime':b,'limit':limit})
            elif provider=='bybit':
                iv='1' if interval=='1m' else '5'; data=_request_json(s,os.getenv('BYBIT_API_BASE','https://api.bybit.com')+'/v5/market/kline',{'category':'linear','symbol':symbol,'interval':iv,'start':a,'end':b,'limit':limit})
            elif provider=='okx':
                bar='1m' if interval=='1m' else '5m'; inst=symbol.replace('USDT','-USDT-SWAP')
                # OKX history-candles: after=older-than cursor, before=newer-than cursor.
                data=_request_json(s,'https://www.okx.com/api/v5/market/history-candles',{'instId':inst,'bar':bar,'after':b,'before':a,'limit':limit})
            elif provider=='bitget':
                gran='1m' if interval=='1m' else '5m'; data=_request_json(s,'https://api.bitget.com/api/v2/mix/market/candles',{'symbol':symbol,'productType':'USDT-FUTURES','granularity':gran,'startTime':a,'endTime':b,'limit':limit})
            elif provider=='gate':
                iv='1m' if interval=='1m' else '5m'; contract=symbol.replace('USDT','_USDT'); data=_request_json(s,'https://api.gateio.ws/api/v4/futures/usdt/candlesticks',{'contract':contract,'interval':iv,'from':int(cursor.timestamp()),'to':int(stop.timestamp()),'limit':limit})
            elif provider=='mexc':
                iv='Min1' if interval=='1m' else 'Min5'; sym=symbol.replace('USDT','_USDT'); data=_request_json(s,f'https://contract.mexc.com/api/v1/contract/kline/{sym}',{'interval':iv,'start':int(cursor.timestamp()),'end':int(stop.timestamp())})
            else:return [],'unsupported_provider'
            rows=_normalize(provider,data,interval)
            out.extend(r for r in rows if a <= r[0] <= b)
            cursor=stop
            if cursor < end: time.sleep(float(os.getenv('EXECUTION_BACKFILL_PAGE_DELAY_SECONDS','0.03')))
        dedup={r[0]:r for r in out}
        return sorted(dedup.values(),key=lambda x:x[0]),None
    except Exception as exc:
        return sorted({r[0]:r for r in out}.values(),key=lambda x:x[0]),f'{type(exc).__name__}: {exc}'

def _candles_any(symbol,start,end,interval='5m',preferred=None,session=None):
    attempts=[]; order=[]
    for p in ([preferred] if preferred else [])+PROVIDERS:
        if p and p not in order:order.append(p)
    for provider in order:
        rows,error=_fetch(provider,symbol,start,end,interval,session); attempts.append({'provider':provider,'rows':len(rows),'interval':interval,'error':error})
        if rows:return rows,provider,attempts
    return [],None,attempts

def _resolve(row,candles,minute_loader=None):
    side=_side(row.get('direction')); entry=_num(row.get('actual_entry') or row.get('target_entry')); stop=_num(row.get('stop')); tp1=_num(row.get('tp1')); filled=_dt(row.get('filled_at'))
    if not (entry>0 and filled and stop>0 and tp1>0):return {'outcome':'UNRESOLVED'}
    best=0.0; worst=0.0; bars=0
    for c in candles:
        o,high,low,close,endms=c; end=datetime.fromtimestamp(endms/1000,tz=timezone.utc)
        if end<=filled:continue
        bars+=1; fav=(high-entry)/entry*100 if side=='LONG' else (entry-low)/entry*100; adv=(low-entry)/entry*100 if side=='LONG' else (entry-high)/entry*100
        best=max(best,fav); worst=min(worst,adv)
        sl=low<=stop if side=='LONG' else high>=stop; tp=high>=tp1 if side=='LONG' else low<=tp1
        if sl and tp:
            if minute_loader:
                mins=minute_loader(datetime.fromtimestamp(o/1000,tz=timezone.utc),end)
                refined=_resolve(row,mins,None)
                if refined.get('outcome') not in {'AMBIGUOUS','UNRESOLVED','TIME_EXIT'}:
                    refined['ambiguous_same_candle']=True; return refined
            return {'outcome':'AMBIGUOUS','exit_reason':'AMBIGUOUS','ambiguous_same_candle':True,'mfe_pct':best,'mae_pct':worst,'bars_to_exit':bars}
        if sl or tp:
            reason='SL' if sl else 'TP1'; exit_price=stop if sl else tp1
            gross=((exit_price-entry)/entry*100) if side=='LONG' else ((entry-exit_price)/entry*100)
            net=gross-2*float(os.getenv('PAPER_FEE_PCT_PER_SIDE','0.06'))-2*float(os.getenv('PAPER_SLIPPAGE_PCT','0.03'))
            risk=abs(entry-stop)/entry*100
            return {'outcome':reason,'exit_reason':reason,'exit_at':end.isoformat(),'net_return_pct':net,'r_multiple':net/risk if risk>1e-9 else 0.0,'mfe_pct':best,'mae_pct':worst,'bars_to_exit':bars,'ambiguous_same_candle':False}
    if candles:
        c=candles[-1]; exit_price=float(c[3]); end=datetime.fromtimestamp(c[4]/1000,tz=timezone.utc)
        gross=((exit_price-entry)/entry*100) if side=='LONG' else ((entry-exit_price)/entry*100)
        net=gross-2*float(os.getenv('PAPER_FEE_PCT_PER_SIDE','0.06'))-2*float(os.getenv('PAPER_SLIPPAGE_PCT','0.03'))
        risk=abs(entry-stop)/entry*100
        return {'outcome':'TIME_EXIT','exit_reason':'TIME_EXIT','exit_at':end.isoformat(),'net_return_pct':net,'r_multiple':net/risk if risk>1e-9 else 0.0,'mfe_pct':best,'mae_pct':worst,'bars_to_exit':bars,'ambiguous_same_candle':False}
    return {'outcome':'UNRESOLVED'}

def _client():
    from cloud_client import get_supabase_client
    return get_supabase_client()

def _paged(table,limit,order='created_at'):
    out=[]; off=0; page=1000
    while len(out)<limit:
        rows=(_client().table(table).select('*').order(order,desc=False).range(off,min(off+page-1,limit-1)).execute().data or [])
        if not rows:break
        out.extend(rows)
        if len(rows)<page:break
        off+=len(rows)
    return out[:limit]

def _paper_rows(limit):
    try:return _paged('paper_positions',limit,'created_at')
    except Exception:return []

def _learning_payload_map(limit):
    """Best-effort enrichment for legacy sparse shadow payloads by signal fingerprint."""
    out={}
    try:
        for r in _paged('learning_observations',limit,'signal_created_at'):
            features=r.get('features') or {}; meta=r.get('metadata') or {}
            if isinstance(features,str):
                try:features=json.loads(features)
                except Exception:features={}
            if isinstance(meta,str):
                try:meta=json.loads(meta)
                except Exception:meta={}
            fp=str((features or {}).get('fingerprint') or (meta or {}).get('fingerprint') or '')
            if fp and isinstance(features,dict): out[fp]=features
    except Exception:pass
    return out

def _legacy_match_key(row):
    created=_dt(row.get('created_at') or row.get('signal_created_at'))
    bucket=created.replace(second=0,microsecond=0).isoformat() if created else ''
    return '|'.join([str(row.get('symbol') or '').upper(),bucket,_side(row.get('direction')),str(row.get('setup') or '').upper(),f"{_num(row.get('target_entry') or row.get('entry')):.8f}"])

def _learning_payload_maps(limit):
    by_fp=_learning_payload_map(limit); by_key={}
    try:
        for r in _paged('learning_observations',limit,'signal_created_at'):
            features=r.get('features') or {}; meta=r.get('metadata') or {}
            if isinstance(features,str):
                try: features=json.loads(features)
                except Exception: features={}
            if isinstance(meta,str):
                try: meta=json.loads(meta)
                except Exception: meta={}
            if not isinstance(features,dict): continue
            row={'symbol':r.get('symbol') or features.get('symbol'),'created_at':r.get('signal_created_at'),'direction':r.get('direction') or features.get('direction'),'setup':features.get('setup'),'target_entry':features.get('entryPrice') or features.get('entry')}
            by_key[_legacy_match_key(row)]=features
    except Exception: pass
    return by_fp,by_key

def _merge_payload(sparse, rich):
    if not isinstance(sparse,dict): sparse={}
    if not isinstance(rich,dict): return sparse
    merged=dict(rich); merged.update(sparse)
    # Preserve rich nested feature families when the legacy shadow dict omitted them.
    for key in ('aiFactors','timeframes','learningMax2','chronos','reliability','decisionSnapshot'):
        if not sparse.get(key) and rich.get(key) is not None: merged[key]=rich.get(key)
    return merged

def _paper_sample(r):
    payload=r.get('signal_payload') or {}; payload=json.loads(payload) if isinstance(payload,str) else (payload or {})
    status=str(r.get('status') or '').lower(); filled=bool(r.get('opened_at')) and _num(r.get('entry_price'))>0
    entry_status='filled' if filled else ('no_fill' if status in {'cancelled','expired','rejected'} else 'unresolved')
    out='UNRESOLVED'; net_ret=None; rmult=None
    if filled and status=='closed' and r.get('net_pnl') is not None:
        notional=_num(r.get('notional_usd')); net_ret=_num(r.get('net_pnl'))/notional*100 if notional>0 else None
        risk=_num(r.get('stop_distance_pct')); rmult=net_ret/risk if net_ret is not None and risk>1e-9 else None; out=str(r.get('close_reason') or 'CLOSED')
    return {'sample_id':'paper:'+str(r.get('id')),'source_id':str(r.get('id')),'sample_type':'PAPER_EXECUTION' if out!='UNRESOLVED' else ('PAPER_FILL' if filled else 'PAPER_NO_FILL'),'decision_at_signal':'ACCEPTED','fingerprint':r.get('fingerprint'),'symbol':r.get('symbol'),'direction':r.get('side'),'setup':payload.get('setup'),'source':r.get('source'),'signal_created_at':r.get('created_at') or r.get('opened_at'),'entry_status':entry_status,'target_entry':r.get('signal_entry_price') or r.get('entry_price'),'actual_entry':r.get('entry_price'),'filled_at':r.get('opened_at'),'exit_at':r.get('closed_at'),'exit_reason':r.get('close_reason'),'outcome':out,'net_return_pct':net_ret,'r_multiple':rmult,'provider':r.get('execution_provider'),'provider_attempts':[],'candle_interval':'paper','ambiguous_same_candle':False,'label_version':'paper_verified_v56','sample_weight':float(os.getenv('EXECUTION_PAPER_SAMPLE_WEIGHT','4.0')),'feature_payload':payload,'updated_at':datetime.now(timezone.utc).isoformat()}

def backfill(limit=10000,dry_run=False):
    shadows=_paged('shadow_signals_v22',limit,'created_at'); sess=requests.Session(); done=unresolved=ambiguous=errors=0
    client=_client(); rich_by_fp,rich_by_key=_learning_payload_maps(limit)
    for row in shadows:
        try:
            created=_dt(row.get('created_at')); filled=_dt(row.get('filled_at')); status=str(row.get('status') or '').lower(); payload=row.get('payload') or {}; payload=json.loads(payload) if isinstance(payload,str) else (payload or {}); fp=str(payload.get('fingerprint') or ''); payload=_merge_payload(payload,rich_by_fp.get(fp) or rich_by_key.get(_legacy_match_key(row)))
            base={'sample_id':'shadow:'+str(row['id']),'source_id':row['id'],'sample_type':'SHADOW_EXECUTION' if filled else 'SHADOW_NO_FILL','decision_at_signal':str(payload.get('decisionAtSignal') or 'REJECTED'),'fingerprint':payload.get('fingerprint'),'symbol':row.get('symbol'),'direction':row.get('direction'),'setup':row.get('setup'),'source':row.get('source'),'signal_created_at':row.get('created_at'),'target_entry':row.get('target_entry'),'actual_entry':row.get('actual_entry'),'filled_at':row.get('filled_at'),'feature_payload':payload,'label_version':'first_hit_v56','sample_weight':1.0,'updated_at':datetime.now(timezone.utc).isoformat(),'ambiguous_same_candle':False}
            if status=='expired' and not filled:base.update(entry_status='no_fill',outcome='NO_FILL',provider_attempts=[])
            elif filled:
                end=min(filled+timedelta(hours=float(os.getenv('EXECUTION_BACKFILL_MAX_HOLD_HOURS','72'))),datetime.now(timezone.utc)); preferred=str(payload.get('executionProvider') or payload.get('marketProvider') or '').lower() or None
                candles,provider,attempts=_candles_any(str(row.get('symbol')),filled,end,'5m',preferred,sess)
                def mins(a,b):return _candles_any(str(row.get('symbol')),a,b,'1m',provider,sess)[0]
                res=_resolve(row,candles,mins); base.update(entry_status='filled',provider=provider,provider_attempts=attempts,candle_interval='5m',**res)
                if res.get('outcome')=='AMBIGUOUS':ambiguous+=1; unresolved+=1
                elif res.get('outcome')=='UNRESOLVED':unresolved+=1
            else:base.update(entry_status='unresolved',outcome='UNRESOLVED',provider_attempts=[]); unresolved+=1
            if filled and created:base['fill_delay_minutes']=(filled-created).total_seconds()/60
            if not dry_run:client.table('execution_training_dataset_v56').upsert(base,on_conflict='sample_id').execute()
            done+=1
        except Exception as exc:errors+=1; print('ERROR',row.get('symbol'),row.get('id'),type(exc).__name__,exc)
    paper_written=0
    for r in _paper_rows(limit):
        try:
            sample=_paper_sample(r)
            if not dry_run:client.table('execution_training_dataset_v56').upsert(sample,on_conflict='sample_id').execute()
            paper_written+=1
        except Exception as exc:errors+=1; print('PAPER_ERROR',r.get('id'),type(exc).__name__,exc)
    return {'status':'ok','shadow_rows':len(shadows),'shadow_written':done,'paper_written':paper_written,'unresolved':unresolved,'ambiguous':ambiguous,'errors':errors,'dry_run':dry_run}

def main():
    p=argparse.ArgumentParser(); p.add_argument('--limit',type=int,default=int(os.getenv('EXECUTION_BACKFILL_MAX_ROWS','10000'))); p.add_argument('--dry-run',action='store_true'); a=p.parse_args(); print(json.dumps(backfill(a.limit,a.dry_run),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
