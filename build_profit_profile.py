"""V53 execution-first profit profile builder.

The runtime profile is a diagnostic/prior layer, not a source of truth for a trade.
V53 fixes the legacy 24h-label problem by preferring canonical filled+closed Paper
execution outcomes whenever a signal fingerprint can be matched. Mark-to-market
horizons are retained only as an explicit fallback/auxiliary target.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Callable, Dict, Iterable, List, Optional

PROFILE_SCHEMA_VERSION = 54
TARGET_TYPE = "execution_first_v54"
BASE_GROUPS = ('setup','regime','structure1h','structure15m','tf1d','tf4h','tf1h','tf15m','tf5m','symbol')
DIRECTION_GROUPS = ('setup','regime','structure1h','structure15m','tf1d','tf4h','tf1h','tf15m','tf5m','symbol')
GROUPS = BASE_GROUPS + tuple(f"{x}_direction" for x in DIRECTION_GROUPS)
FACTORS = ('trend','momentum','volume','funding','open_interest','alignment','risk_reward','capital_flow','smart_money','news','narrative')
DEFAULT_WINDOWS = (7, 14, 21, 30, 60, 90, 180)


def _json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict): return value
    if not value: return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _num(value: Any, default: float = 0.0) -> float:
    try: return float(value)
    except Exception: return default


def _dt(value: Any) -> datetime | None:
    if not value: return None
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _direction(value: Any) -> str:
    v = str(value or '').upper().replace('_BIAS','')
    if v in {'BUY','LONG'}: return 'LONG'
    if v in {'SELL','SHORT'}: return 'SHORT'
    return v or 'UNKNOWN'


def _fingerprint(source: Dict[str, Any], features: Dict[str, Any]) -> str:
    meta = _json(source.get('metadata'))
    return str(source.get('fingerprint') or features.get('fingerprint') or meta.get('fingerprint') or '')


def _normalize_observation(source: Dict[str, Any]) -> Dict[str, Any] | None:
    f = _json(source.get('features') or source.get('payload_json') or source.get('signal_payload'))
    result = _json(source.get('real_result') or source.get('result') or source.get('outcome'))
    factors = f.get('aiFactors') or f.get('tradeProfile') or {}
    target_horizon = str(os.getenv('LEARNING_TARGET_HORIZON', '24h')).lower()
    returns = _json(result.get('returns'))
    ret: Optional[float] = None
    if returns:
        if target_horizon not in returns: return None
        ret = _num(returns.get(target_horizon), 0.0)
    else:
        horizon = str(result.get('horizon') or '').lower()
        if horizon and horizon != target_horizon: return None
        raw = result.get('return_percent', result.get('returnPercent', result.get('pnl_percent')))
        if raw is None: return None
        ret = _num(raw, 0.0)
    success = result.get('success')
    if success is None: success = ret > 0
    created = source.get('signal_created_at') or source.get('created_at') or f.get('createdAt') or f.get('timestamp')
    item = {
        'fingerprint': _fingerprint(source, f), 'created_at': created, '_dt': _dt(created),
        'symbol': source.get('symbol') or f.get('symbol') or 'UNKNOWN',
        'direction': _direction(source.get('signal_direction') or f.get('direction')),
        'return': ret, 'win': 1 if bool(success) else 0, 'target_source': 'mark_to_market',
        'setup': str(f.get('setup', 'NONE')).upper(),
        'regime': f.get('marketRegime') or f.get('aiRegime') or 'unknown',
        'structure1h': f.get('structure1h', 'N/A'), 'structure15m': f.get('structure15m', 'N/A'),
        'score': _num(source.get('signal_score') or f.get('aiScore') or f.get('score')),
        'probability': _num(source.get('calibrated_probability') or f.get('calibratedProbability') or f.get('probability') or source.get('signal_confidence')),
        'confidence': _num(f.get('confidence')), 'uncertainty': _num(f.get('uncertainty'), 100),
        'quoteVolume': _num(f.get('quoteVolume')), 'rr': _num(f.get('rr')),
        'net_pnl': None, 'r_multiple': None,
    }
    tfs = f.get('timeframes') or {}
    for tf in ('1d','4h','1h','15m','5m'): item['tf' + tf] = tfs.get(tf, 'N/A')
    for key in FACTORS: item[key] = _num(factors.get(key), 50.0)
    _add_direction_keys(item)
    return item


def _normalize(source: Dict[str, Any]) -> Dict[str, Any] | None:
    """Backward-compatible alias for observation normalization."""
    return _normalize_observation(source)


def _normalize_execution(source: Dict[str, Any]) -> Dict[str, Any] | None:
    payload = _json(source.get('signal_payload') or source.get('payload_json'))
    factors = payload.get('aiFactors') or payload.get('tradeProfile') or {}
    if not factors: return None
    status = str(source.get('status') or 'closed').lower()
    if status not in {'closed','filled_closed','completed'} and source.get('closed_at') in {None,''}: return None
    if str(source.get('execution_verified', 'true')).lower() in {'false','0','no'}: return None
    entry = _num(source.get('entry_price'))
    exit_price = _num(source.get('exit_price'))
    side = _direction(source.get('side') or payload.get('direction'))
    pnl = _num(source.get('net_pnl'))
    if entry <= 0 or exit_price <= 0: return None
    directional_price_return = (exit_price-entry)/entry*100.0
    if side == 'SHORT': directional_price_return *= -1.0
    stop = _num(source.get('stop_price') or payload.get('stop'))
    risk_pct = abs(entry-stop)/entry*100.0 if stop > 0 else 0.0
    # Execution return is net-PnL aware for the label; price return is used for robust
    # cross-trade magnitude so leverage does not dominate profile statistics.
    r_multiple = directional_price_return/risk_pct if risk_pct > 1e-9 else 0.0
    created = source.get('opened_at') or payload.get('signal_created_at') or source.get('created_at') or source.get('closed_at')
    item = {
        'fingerprint': str(source.get('fingerprint') or payload.get('fingerprint') or ''),
        'created_at': created, '_dt': _dt(created), 'symbol': source.get('symbol') or payload.get('symbol') or 'UNKNOWN',
        'direction': side, 'return': directional_price_return, 'win': 1 if pnl > 1e-9 else 0,
        'target_source': 'paper_execution', 'net_pnl': pnl, 'r_multiple': r_multiple,
        'close_reason': str(source.get('close_reason') or ''),
        'setup': str(payload.get('setup','NONE')).upper(),
        'regime': payload.get('marketRegime') or payload.get('aiRegime') or 'unknown',
        'structure1h': payload.get('structure1h','N/A'), 'structure15m': payload.get('structure15m','N/A'),
        'score': _num(payload.get('aiScore') or payload.get('score')),
        'probability': _num(source.get('probability') or payload.get('calibratedProbability') or payload.get('probability')),
        'confidence': _num(payload.get('confidence')), 'uncertainty': _num(payload.get('uncertainty'),100),
        'quoteVolume': _num(payload.get('quoteVolume')), 'rr': _num(payload.get('rr')),
    }
    tfs = payload.get('timeframes') or {}
    for tf in ('1d','4h','1h','15m','5m'): item['tf'+tf] = tfs.get(tf,'N/A')
    for key in FACTORS: item[key] = _num(factors.get(key),50.0)
    _add_direction_keys(item)
    return item


def _add_direction_keys(item: Dict[str, Any]) -> None:
    d = item.get('direction') or 'UNKNOWN'
    for key in DIRECTION_GROUPS:
        item[f'{key}_direction'] = f"{item.get(key,'N/A')}|{d}"


def _merge_rows(observations: Iterable[Dict[str, Any]], executions: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    obs = [x for raw in observations if (x := _normalize_observation(raw)) is not None]
    exe = [x for raw in executions if (x := _normalize_execution(raw)) is not None]
    by_fp = {x['fingerprint']: x for x in exe if x.get('fingerprint')}
    merged: List[Dict[str, Any]] = []
    seen = set()
    for item in obs:
        fp = item.get('fingerprint')
        execution = by_fp.get(fp) if fp else None
        if execution:
            # Preserve rich observation features but replace the target with actual execution.
            upgraded = dict(item)
            for key in ('return','win','target_source','net_pnl','r_multiple','close_reason','created_at','_dt'):
                if key in execution: upgraded[key] = execution[key]
            # Execution payload may contain fresher signal metadata; prefer it when present.
            for key in ('setup','regime','direction','structure1h','structure15m','probability','score','confidence','uncertainty','quoteVolume','rr'):
                if execution.get(key) not in (None,'','N/A','UNKNOWN'): upgraded[key] = execution[key]
            for key in FACTORS:
                if execution.get(key) is not None: upgraded[key] = execution[key]
            for tf in ('1d','4h','1h','15m','5m'):
                k='tf'+tf
                if execution.get(k) not in (None,'','N/A'): upgraded[k]=execution[k]
            _add_direction_keys(upgraded)
            merged.append(upgraded); seen.add(fp)
        else:
            merged.append(item)
    for item in exe:
        fp=item.get('fingerprint')
        if not fp or fp not in seen: merged.append(item)
    merged.sort(key=lambda r: r.get('_dt') or datetime.min.replace(tzinfo=timezone.utc))
    return merged


def _winsorized(values: List[float], q: float = 0.05) -> List[float]:
    if len(values) < 20: return [max(-25.0,min(25.0,x)) for x in values]
    xs=sorted(values); lo=xs[int((len(xs)-1)*q)]; hi=xs[int((len(xs)-1)*(1-q))]
    lo=max(-25.0,lo); hi=min(25.0,hi)
    return [max(lo,min(hi,x)) for x in values]


def _stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    n=len(rows)
    if not n:
        return {'samples':0,'win_rate':0.0,'avg_return':0.0,'robust_avg_return':0.0,'median_return':0.0,'avg_win':0.0,'avg_loss':0.0,'profit_factor':0.0,'robust_profit_factor':0.0,'expectancy_r':0.0,'execution_samples':0}
    wins=sum(int(r['win']) for r in rows); returns=[_num(r['return']) for r in rows]; robust=_winsorized(returns)
    pos=[x for x in returns if x>0]; neg=[x for x in returns if x<0]
    rvals=[_num(r.get('r_multiple')) for r in rows if r.get('r_multiple') is not None]
    gp=sum(max(0,x) for x in returns); gl=sum(abs(min(0,x)) for x in returns)
    rgp=sum(max(0,x) for x in robust); rgl=sum(abs(min(0,x)) for x in robust)
    target_counts=defaultdict(int)
    for r in rows: target_counts[str(r.get('target_source') or 'unknown')]+=1
    execution=[r for r in rows if r.get('target_source')=='paper_execution']
    ex_returns=[_num(r.get('return')) for r in execution]; ex_robust=_winsorized(ex_returns) if execution else []
    ex_wins=sum(int(r.get('win',0)) for r in execution); ex_gp=sum(max(0,x) for x in ex_robust); ex_gl=sum(abs(min(0,x)) for x in ex_robust)
    return {
        'samples':n,'win_rate':round(wins/n*100,2),'avg_return':round(sum(returns)/n,4),
        'robust_avg_return':round(sum(robust)/n,4),'median_return':round(float(median(returns)),4),
        'avg_win':round(sum(pos)/len(pos),4) if pos else 0.0,'avg_loss':round(abs(sum(neg)/len(neg)),4) if neg else 0.0,
        'profit_factor':round(gp/gl,4) if gl else (99.0 if gp else 0.0),
        'robust_profit_factor':round(rgp/rgl,4) if rgl else (99.0 if rgp else 0.0),
        'expectancy_r':round(sum(rvals)/len(rvals),4) if rvals else None,
        'execution_samples':len(execution),'execution_win_rate':round(ex_wins/len(execution)*100,2) if execution else None,
        'execution_robust_avg_return':round(sum(ex_robust)/len(ex_robust),4) if ex_robust else None,
        'execution_robust_profit_factor':round(ex_gp/ex_gl,4) if ex_gl else (99.0 if ex_gp else (0.0 if execution else None)),
        'target_sources':dict(target_counts),
    }


def _group_min_samples(group: str) -> int:
    if group.startswith('symbol'): return max(20,int(os.getenv('PROFILE_SYMBOL_MIN_SAMPLES','50')))
    if group.endswith('_direction'): return max(12,int(os.getenv('PROFILE_DIRECTION_GROUP_MIN_SAMPLES','30')))
    return max(8,int(os.getenv('PROFILE_GROUP_MIN_SAMPLES','20')))


def _groups(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    out={}
    for col in GROUPS:
        bucket=defaultdict(list)
        for row in rows: bucket[str(row.get(col,'N/A'))].append(row)
        mn=_group_min_samples(col)
        out[col]={key:_stats(items) for key,items in bucket.items() if len(items)>=mn}
    return out


def _auc(rows: List[Dict[str, Any]], score_key: str='probability') -> Optional[float]:
    pairs=[(_num(r.get(score_key)),int(r.get('win',0))) for r in rows if r.get(score_key) is not None]
    pos=sum(y for _,y in pairs); neg=len(pairs)-pos
    if pos==0 or neg==0: return None
    pairs.sort(key=lambda x:x[0]); rank_sum=0.0; i=0
    while i<len(pairs):
        j=i+1
        while j<len(pairs) and pairs[j][0]==pairs[i][0]: j+=1
        avg_rank=(i+1+j)/2.0
        rank_sum += avg_rank*sum(y for _,y in pairs[i:j]); i=j
    return (rank_sum-pos*(pos+1)/2.0)/(pos*neg)


def _diagnostics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    st=_stats(rows); probs=[max(0.001,min(0.999,_num(r.get('probability'))/100.0)) for r in rows if _num(r.get('probability'))>0]
    ys=[int(r.get('win',0)) for r in rows if _num(r.get('probability'))>0]
    brier=sum((p-y)**2 for p,y in zip(probs,ys))/len(probs) if probs else None
    auc=_auc(rows,'probability')
    ex=[r for r in rows if r.get('target_source')=='paper_execution']; ex_auc=_auc(ex,'probability') if ex else None
    ex_probs=[max(0.001,min(0.999,_num(r.get('probability'))/100.0)) for r in ex if _num(r.get('probability'))>0]; ex_ys=[int(r.get('win',0)) for r in ex if _num(r.get('probability'))>0]
    ex_brier=sum((p-y)**2 for p,y in zip(ex_probs,ex_ys))/len(ex_probs) if ex_probs else None
    st.update({'probability_auc':round(auc,4) if auc is not None else None,'probability_brier':round(brier,4) if brier is not None else None,
               'execution_probability_auc':round(ex_auc,4) if ex_auc is not None else None,'execution_probability_brier':round(ex_brier,4) if ex_brier is not None else None})
    return st


def _rule(rows: List[Dict[str, Any]], kind: str, name: str, pred: Callable[[Dict[str, Any]],bool], adjustment: float) -> Dict[str, Any] | None:
    subset=[r for r in rows if pred(r)]
    if len(subset)<max(20,int(os.getenv('PROFILE_RULE_MIN_SAMPLES','30'))): return None
    item=_stats(subset); item.update({'kind':kind,'name':name,'adjustment':adjustment}); return item


def _rule_specs():
    # Setup itself is deliberately NOT repeated here: setup is already modeled by
    # setup_direction specialist groups, preventing v52 double counting.
    return [
      ('boost','flow_alignment_volume',lambda r:r['capital_flow']>=62 and r['alignment']>=75 and r['volume']>=65,6),
      ('boost','smart_pullback_volume',lambda r:r['smart_money']>=60 and str(r['setup']).upper()=='PULLBACK' and r['volume']>=65,4),
      ('boost','daily_micro_alignment_long',lambda r:r['direction']=='LONG' and r['tf1d']=='UP' and r['tf5m']=='UP' and r['alignment']>=75,4),
      ('boost','daily_micro_alignment_short',lambda r:r['direction']=='SHORT' and r['tf1d']=='DOWN' and r['tf5m']=='DOWN' and r['alignment']>=75,4),
      ('penalty','low_liquidity',lambda r:r['quoteVolume']<130_000_000,-8),
      ('penalty','weak_capital_flow',lambda r:r['capital_flow']<=50,-6),
      ('penalty','low_confidence',lambda r:r['confidence']<36,-6),
      ('penalty','high_uncertainty',lambda r:r['uncertainty']>64,-6),
      ('penalty','long_against_4h',lambda r:r['direction']=='LONG' and r['tf4h']=='DOWN',-10),
      ('penalty','short_against_4h',lambda r:r['direction']=='SHORT' and r['tf4h']=='UP',-10),
      ('penalty','weak_breakout',lambda r:str(r['setup']).upper()=='BREAKOUT' and r['volume']<60,-8),
      ('penalty','breakout_micro_weak_long',lambda r:r['direction']=='LONG' and str(r['setup']).upper()=='BREAKOUT' and r['tf5m'] in ('DOWN','RANGE'),-6),
      ('penalty','breakout_micro_weak_short',lambda r:r['direction']=='SHORT' and str(r['setup']).upper()=='BREAKOUT' and r['tf5m'] in ('UP','RANGE'),-6),
    ]


def _rules(rows): return [x for x in (_rule(rows,*spec) for spec in _rule_specs()) if x]


def _load_csv(path: Path) -> List[Dict[str, Any]]:
    with path.open('r',encoding='utf-8-sig',newline='') as fh: return list(csv.DictReader(fh))


def _load_supabase(limit: int) -> List[Dict[str, Any]]:
    from cloud_learning_store import CloudLearningStore
    return list(CloudLearningStore().resolved_rows(limit=limit))


def _load_paper_supabase(limit: int) -> List[Dict[str, Any]]:
    try:
        from repositories.paper_repository import PaperRepository
        return list(PaperRepository().all_valid_closed_positions(limit, ascending=True))
    except Exception:
        return []


def _dataset_hash(rows: List[Dict[str, Any]]) -> str:
    h=hashlib.sha256()
    for r in rows:
        h.update((str(r.get('fingerprint'))+'|'+str(r.get('target_source'))+'|'+str(r.get('return'))+'\n').encode())
    return h.hexdigest()


def build(rows_raw: Iterable[Dict[str, Any]], windows: Iterable[int]=DEFAULT_WINDOWS, execution_rows: Iterable[Dict[str, Any]] | None=None) -> Dict[str, Any]:
    rows=_merge_rows(list(rows_raw), list(execution_rows or []))
    if not rows: raise RuntimeError('no usable resolved observations found')
    now=datetime.now(timezone.utc); windows=sorted({max(1,int(x)) for x in windows})
    recent_windows={}; recent_overall={}; recent_rules={}
    for days in windows:
        cutoff=now-timedelta(days=days); recent=[r for r in rows if r.get('_dt') and r['_dt']>=cutoff]
        recent_windows[str(days)]=_groups(recent)
        recent_overall[str(days)]=_diagnostics(recent)
        recent_rules[str(days)]={r['name']:r for r in _rules(recent)}
    source_counts=defaultdict(int)
    for r in rows: source_counts[str(r.get('target_source') or 'unknown')]+=1
    latest=max((r['_dt'] for r in rows if r.get('_dt')),default=None)
    profile={
      'schema_version':PROFILE_SCHEMA_VERSION,'version':'profit-profile-v53-'+now.strftime('%Y%m%d%H%M%S'),
      'generated_at':now.isoformat(),'target_type':TARGET_TYPE,'target_horizon':str(os.getenv('LEARNING_TARGET_HORIZON','24h')).lower(),
      'target_source_counts':dict(source_counts),'dataset_hash':_dataset_hash(rows),'latest_observation_at':latest.isoformat() if latest else None,
      'overall':_diagnostics(rows),'groups':_groups(rows),'recent_windows':recent_windows,'recent_overall':recent_overall,
      'recent_rule_diagnostics':recent_rules,'recent_window_options':windows,'rule_diagnostics':_rules(rows),
      'profile_policy':{'symbol_min_samples':_group_min_samples('symbol'),'direction_group_min_samples':_group_min_samples('setup_direction'),'setup_is_specialist':True,'execution_first':True}
    }
    return profile


def validate_profile(profile: Dict[str,Any]) -> tuple[bool,list[str]]:
    reasons=[]
    if int(profile.get('schema_version') or 0)<PROFILE_SCHEMA_VERSION: reasons.append('legacy_schema')
    if profile.get('target_type')!=TARGET_TYPE: reasons.append('wrong_target_type')
    if not profile.get('generated_at'): reasons.append('missing_generated_at')
    if not profile.get('dataset_hash'): reasons.append('missing_dataset_hash')
    if not isinstance(profile.get('recent_windows'),dict): reasons.append('missing_recent_windows')
    return not reasons,reasons


def _atomic_write_json(path: Path,payload:Dict[str,Any])->None:
    path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_name(path.name+'.tmp')
    data=json.dumps(payload,ensure_ascii=False,indent=2,default=str)
    with tmp.open('w',encoding='utf-8') as fh:
        fh.write(data); fh.flush(); os.fsync(fh.fileno())
    os.replace(tmp,path)


def rebuild_from_supabase(output: str|Path|None=None, limit: int|None=None, windows: Iterable[int]=DEFAULT_WINDOWS)->Dict[str,Any]:
    max_rows=int(limit or os.getenv('PROFILE_REBUILD_MAX_ROWS','10000'))
    obs=_load_supabase(max(100,max_rows)); paper=_load_paper_supabase(max(100,max_rows))
    profile=build(obs,windows,execution_rows=paper)
    out=Path(output or os.getenv('PROFIT_PROFILE_PATH','data/profit_profile_v2.json')); _atomic_write_json(out,profile)
    try:
        import ai_hedge_fund_engine; ai_hedge_fund_engine.invalidate_profile_cache()
    except Exception: pass
    return {'status':'ok','path':str(out),'samples':profile['overall']['samples'],'execution_samples':profile['target_source_counts'].get('paper_execution',0),'windows':profile['recent_window_options'],'version':profile['version']}


def main()->None:
    p=argparse.ArgumentParser(); p.add_argument('--input'); p.add_argument('--paper-input'); p.add_argument('--output',default=os.getenv('PROFIT_PROFILE_PATH','data/profit_profile_v2.json'))
    p.add_argument('--limit',type=int,default=int(os.getenv('PROFILE_REBUILD_MAX_ROWS','10000'))); p.add_argument('--windows',default=os.getenv('PROFILE_RECENT_WINDOWS_DAYS','7,14,21,30,60,90,180'))
    args=p.parse_args(); obs=_load_csv(Path(args.input)) if args.input else _load_supabase(max(100,args.limit)); paper=_load_csv(Path(args.paper_input)) if args.paper_input else ([] if args.input else _load_paper_supabase(max(100,args.limit)))
    windows=[int(x.strip()) for x in args.windows.split(',') if x.strip()]; profile=build(obs,windows,execution_rows=paper); out=Path(args.output); _atomic_write_json(out,profile)
    print(f"{out} samples={profile['overall']['samples']} execution={profile['target_source_counts'].get('paper_execution',0)} windows={profile['recent_window_options']}")

if __name__=='__main__': main()
