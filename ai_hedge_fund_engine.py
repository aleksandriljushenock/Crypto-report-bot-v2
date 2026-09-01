"""V53 signal-quality ensemble.

Key principles:
- only a valid execution-first V53 profile can change live probability;
- setup/direction specialists are used before generic historical groups;
- win probability and expectancy are separate concepts;
- no feature is counted twice through setup + manual rules + EV + quality;
- reliability, uncertainty and recent degradation are explicit gates.
"""
from __future__ import annotations
import json, math, os, time
from pathlib import Path
from typing import Any, Dict, Optional
from core.runtime_config import boolean, integer, number, string

PROFILE_PATH=Path(string('PROFIT_PROFILE_PATH','data/profit_profile_v2.json', strategy=False))
_CACHE=None
_EXECUTION_CACHE={'at':0.0,'rows':[]}


def _env_float(name,default):
    try:return number(name,float(default))
    except Exception:return float(default)
def _env_int(name,default):
    try:return integer(name,int(default))
    except Exception:return int(default)
def _env_bool(name,default=False): return boolean(name,default)
def _clamp(v,lo=0.0,hi=100.0):
    try:return max(lo,min(hi,float(v)))
    except Exception:return lo

def invalidate_profile_cache():
    global _CACHE; _CACHE=None

def _fallback_profile(reason='missing'):
    return {'schema_version':55,'version':'v55-safe-fallback','target_type':'execution_first_v55','valid':False,'validation_reasons':[reason],
            'overall':{'win_rate':50.0,'robust_avg_return':0.0,'robust_profit_factor':1.0},'groups':{},'recent_windows':{},'recent_overall':{},'rule_diagnostics':[]}

def _profile():
    global _CACHE
    if _CACHE is None:
        try:
            raw=json.loads(PROFILE_PATH.read_text(encoding='utf-8'))
            from build_profit_profile import validate_profile
            ok,reasons=validate_profile(raw); raw['valid']=bool(ok); raw['validation_reasons']=list(reasons)
            # Legacy profiles remain visible for diagnostics but cannot modify trading.
            _CACHE=raw
        except Exception as exc:
            _CACHE=_fallback_profile(type(exc).__name__)
    return _CACHE

def _group_stat(group,key): return ((_profile().get('groups') or {}).get(group) or {}).get(str(key)) or {}

def _execution_calibration(signal:Dict[str,Any])->Dict[str,Any]:
    global _EXECUTION_CACHE
    ttl=max(30,_env_int('EXECUTION_CALIBRATION_CACHE_SECONDS',300)); now=time.time()
    if now-float(_EXECUTION_CACHE.get('at') or 0)>ttl:
        try:
            from repositories.paper_repository import PaperRepository
            rows=PaperRepository().all_valid_closed_positions(_env_int('EXECUTION_CALIBRATION_MAX_TRADES',2000),ascending=True)
            _EXECUTION_CACHE={'at':now,'rows':rows}
        except Exception:_EXECUTION_CACHE={'at':now,'rows':[]}
    rows=list(_EXECUTION_CACHE.get('rows') or []); minimum=max(12,_env_int('EXECUTION_CALIBRATION_MIN_TRADES',20))
    if len(rows)<minimum:return {'available':False,'samples':len(rows)}
    direction=str(signal.get('direction') or '').upper().replace('_BIAS',''); setup=str(signal.get('setup') or '').upper()
    exact=[]; directional=[]
    for row in rows:
        payload=row.get('signal_payload') or {}
        if isinstance(payload,str):
            try:payload=json.loads(payload)
            except Exception:payload={}
        rd=str(row.get('side') or payload.get('direction') or '').upper().replace('_BIAS',''); rs=str(payload.get('setup') or '').upper()
        if rd==direction: directional.append(row)
        if rd==direction and setup and rs==setup: exact.append(row)
    selected=exact if len(exact)>=minimum else (directional if len(directional)>=minimum else rows)
    valid=[r for r in selected if abs(float(r.get('net_pnl') or 0))>1e-9]; n=len(valid)
    if n<minimum:return {'available':False,'samples':n}
    wins=sum(1 for r in valid if float(r.get('net_pnl') or 0)>0); losses=n-wins
    # Jeffreys/Beta-style shrinkage; confidence grows slowly with n.
    rate=(wins+4.0)/(n+8.0)*100.0
    return {'available':True,'samples':n,'wins':wins,'losses':losses,'probability':round(rate,2),'scope':'direction_setup' if selected is exact else ('direction' if selected is directional else 'global')}

def _bayes_rate(prior_pct,wins_pct,n,strength=40.0):
    if not n:return prior_pct
    return (prior_pct*strength+wins_pct*n)/(strength+n)

def _recent_group_stat(group,key):
    p=_profile(); requested=max(1,_env_int('PROFILE_RECENT_WINDOW_DAYS',21)); windows=p.get('recent_windows') or {}
    available=[]
    for raw in windows:
        try:available.append(int(raw))
        except Exception:pass
    if available:
        selected=min(available,key=lambda d:abs(d-requested)); groups=windows.get(str(selected)) or windows.get(selected) or {}
        return ((groups.get(group) or {}).get(str(key)) or {}),selected
    return {},None

def _blend_recent_rate(base_rate,group,key):
    if not _env_bool('PROFILE_RECENCY_ENABLED',True):return base_rate,None
    st,days=_recent_group_stat(group,key); n=int(st.get('samples') or 0); mn=max(20,_env_int('PROFILE_MIN_RECENT_SAMPLES',30))
    if n<mn:return base_rate,None
    recent=float(st.get('win_rate') or base_rate); configured=max(0.0,_env_float('PROFILE_RECENT_WEIGHT',2.0)); half=max(1.0,_env_float('PROFILE_HALF_LIFE_DAYS',14.0)); window=float(days or 21)
    w=configured*(0.5**(window/half)); blended=(base_rate+recent*w)/(1+w) if w>0 else base_rate
    return blended,{'samples':n,'win_rate':recent,'weight':round(w,4),'window_days':days}

def _edge_from_stat(st:Dict[str,Any])->float:
    """Return independent historical expectancy evidence in [-1,1]."""
    if not st:return 0.0
    ex_n=int(st.get('execution_samples') or 0); ex_min=max(12,_env_int('PROFILE_MIN_EXECUTION_CONTEXT_SAMPLES',20))
    if ex_n>=ex_min and st.get('execution_robust_avg_return') is not None:
        ret=float(st.get('execution_robust_avg_return') or 0); pf=float(st.get('execution_robust_profit_factor') or 1)
    else:
        ret=float(st.get('robust_avg_return',st.get('avg_return',0)) or 0); pf=float(st.get('robust_profit_factor',st.get('profit_factor',1)) or 1)
    # Both terms are bounded; extreme pumps cannot dominate.
    return max(-1.0,min(1.0,0.62*math.tanh(ret/3.0)+0.38*math.tanh(math.log(max(0.05,pf))/1.2)))

def _profile_health():
    p=_profile()
    if not p.get('valid'):return {'status':'INVALID','degraded':True,'severe':True,'reasons':p.get('validation_reasons') or ['invalid_profile']}
    req=max(1,_env_int('PROFILE_RECENT_WINDOW_DAYS',21)); ro=p.get('recent_overall') or {}; keys=[]
    for k in ro:
        try:keys.append(int(k))
        except Exception:pass
    if not keys:return {'status':'NO_RECENT_DIAGNOSTICS','degraded':True,'severe':False,'reasons':['no_recent_diagnostics']}
    chosen=min(keys,key=lambda x:abs(x-req)); st=ro.get(str(chosen)) or ro.get(chosen) or {}; reasons=[]
    n=int(st.get('samples') or 0); ex_n=int(st.get('execution_samples') or 0); ex_min=max(20,_env_int('PROFILE_MIN_EXECUTION_HEALTH_SAMPLES',30))
    auc=st.get('execution_probability_auc') if ex_n>=ex_min and st.get('execution_probability_auc') is not None else st.get('probability_auc')
    brier=st.get('execution_probability_brier') if ex_n>=ex_min and st.get('execution_probability_brier') is not None else st.get('probability_brier')
    if ex_n>=ex_min and st.get('execution_robust_avg_return') is not None:
        pf=float(st.get('execution_robust_profit_factor') or 1); ret=float(st.get('execution_robust_avg_return') or 0)
    else:
        pf=float(st.get('robust_profit_factor',st.get('profit_factor',1)) or 1); ret=float(st.get('robust_avg_return',st.get('avg_return',0)) or 0)
    if auc is not None and float(auc)<_env_float('PROFILE_MIN_RECENT_AUC',0.48):reasons.append('probability_auc_below_floor')
    if brier is not None and float(brier)>_env_float('PROFILE_MAX_RECENT_BRIER',0.30):reasons.append('probability_brier_too_high')
    if n>=max(30,_env_int('PROFILE_MIN_RECENT_SAMPLES',30)) and pf<0.90 and ret<0:reasons.append('negative_recent_expectancy')
    # V54 fail-closed execution health: a clearly losing execution ledger must not
    # remain live merely because a tiny-sample AUC happens to be above 0.5.
    severe=False
    if ex_n>=ex_min:
        ex_wr=float(st.get('execution_win_rate')) if st.get('execution_win_rate') is not None else None
        if pf < _env_float('PROFILE_SEVERE_EXECUTION_PF',0.70): reasons.append('execution_profit_factor_critical')
        if ret < _env_float('PROFILE_SEVERE_EXECUTION_RETURN',0.0): reasons.append('execution_expectancy_negative')
        if brier is not None and float(brier)>_env_float('PROFILE_SEVERE_EXECUTION_BRIER',0.35): reasons.append('execution_brier_critical')
        if ex_wr is not None and ex_wr < _env_float('PROFILE_SEVERE_EXECUTION_WIN_RATE',20.0): reasons.append('execution_win_rate_critical')
        severe=any(x in reasons for x in ('execution_profit_factor_critical','execution_brier_critical','execution_win_rate_critical')) or ('execution_expectancy_negative' in reasons and 'probability_auc_below_floor' in reasons)
    else:
        severe=('probability_auc_below_floor' in reasons and 'negative_recent_expectancy' in reasons)
    return {'status':'SEVERE' if severe else ('DEGRADED' if reasons else 'HEALTHY'),'degraded':bool(reasons),'severe':severe,'reasons':list(dict.fromkeys(reasons)),'window_days':chosen,'metrics':st}

def _rule_diagnostic(name):
    p=_profile(); requested=max(1,_env_int('PROFILE_RECENT_WINDOW_DAYS',21)); rr=p.get('recent_rule_diagnostics') or {}; keys=[]
    for k in rr:
        try:keys.append(int(k))
        except Exception:pass
    if keys:
        k=min(keys,key=lambda x:abs(x-requested)); st=(rr.get(str(k)) or rr.get(k) or {}).get(name)
        if st:return st,k
    for st in p.get('rule_diagnostics') or []:
        if st.get('name')==name:return st,None
    return None,None

def _dynamic_adjustment(name,base):
    """Decay stale rules and never let a positive rule reward negative recent expectancy."""
    st,window=_rule_diagnostic(name)
    if not st:return 0.0 if _profile().get('valid') else 0.0
    n=int(st.get('samples') or 0); mn=max(20,_env_int('PROFILE_RULE_MIN_SAMPLES',30))
    if n<mn:return 0.0
    edge=_edge_from_stat(st); sign=1 if base>0 else -1
    if sign>0 and edge<=0:return 0.0
    if sign<0 and edge>=0:return 0.0
    confidence=min(1.0,math.sqrt(n/max(1.0,float(_env_int('PROFILE_RULE_FULL_WEIGHT_SAMPLES',200)))))
    strength=min(1.0,abs(edge)*1.4)
    return round(base*confidence*strength,3)

def _rule_hits(s):
    f=s.get('aiFactors') or {}; setup=str(s.get('setup') or 'NONE').upper(); t=s.get('timeframes') or {}; direction=str(s.get('direction') or '')
    qv=float(s.get('quoteVolume') or 0); conf=float(s.get('confidence') or 0); unc=float(s.get('uncertainty') or s.get('aiUncertainty') or 100)
    vals={k:float(f.get(k,50) or 50) for k in ('trend','volume','alignment','capital_flow','smart_money')}; hits=[]
    def add(name,base,hard=False):
        adj=_dynamic_adjustment(name,base)
        if adj:hits.append({'name':name,'adjustment':adj,'hard_block':hard})
    # Setup is NOT a rule in V53; setup_direction is already a specialist context.
    if vals['capital_flow']>=62 and vals['alignment']>=75 and vals['volume']>=65:add('flow_alignment_volume',6)
    if vals['smart_money']>=60 and setup=='PULLBACK' and vals['volume']>=65:add('smart_pullback_volume',4)
    if direction=='LONG_BIAS' and t.get('1d')=='UP' and t.get('5m')=='UP' and vals['alignment']>=75:add('daily_micro_alignment_long',4)
    if direction=='SHORT_BIAS' and t.get('1d')=='DOWN' and t.get('5m')=='DOWN' and vals['alignment']>=75:add('daily_micro_alignment_short',4)
    if qv<130_000_000:add('low_liquidity',-8,qv<80_000_000)
    if vals['capital_flow']<=50:add('weak_capital_flow',-6,vals['capital_flow']<42)
    if conf<36:add('low_confidence',-6,conf<30)
    if unc>64:add('high_uncertainty',-6,unc>72)
    if direction=='LONG_BIAS' and t.get('4h')=='DOWN':add('long_against_4h',-10,True)
    if direction=='SHORT_BIAS' and t.get('4h')=='UP':add('short_against_4h',-10,True)
    if setup=='BREAKOUT' and vals['volume']<60:add('weak_breakout',-8)
    if setup=='BREAKOUT' and direction=='LONG_BIAS' and t.get('5m') in ('DOWN','RANGE'):add('breakout_micro_weak_long',-6)
    if setup=='BREAKOUT' and direction=='SHORT_BIAS' and t.get('5m') in ('UP','RANGE'):add('breakout_micro_weak_short',-6)
    venues=int(s.get('exchangeCount') or len(s.get('marketExchanges') or [])); move=float(s.get('crossExchangeChangeMedian') or 0)
    # Coverage affects reliability instead of probability; momentum is accepted only directionally.
    if venues>=3 and ((direction=='LONG_BIAS' and move>=3) or (direction=='SHORT_BIAS' and move<=-3)):
        hits.append({'name':'cross_exchange_direction_agreement','adjustment':min(2.0,0.4*venues),'hard_block':False})
    return hits

def _reliability(signal):
    f=signal.get('aiFactors') or {}; penalties=[]; score=100.0
    required=('trend','momentum','volume','funding','alignment','risk_reward','capital_flow','smart_money')
    missing=sum(1 for k in required if k not in f or f.get(k) is None)
    if missing: score-=missing*7; penalties.append(f'missing_features:{missing}')
    source_meta=signal.get('featureSources') or signal.get('dataQuality') or {}
    for key,penalty in (('narrative',6),('smart_money',7),('capital_flow',5)):
        try: val=float(f.get(key,50))
        except Exception: val=50
        if abs(val-50.0)<1e-9 and not (isinstance(source_meta,dict) and source_meta.get(key)):
            score-=penalty; penalties.append(f'{key}_neutral_or_fallback')
    # Known legacy OI neutral/fallback sentinel.
    oi=float(f.get('open_interest',50) or 50); oi_meta=signal.get('oiAnalysis') or {}
    if abs(oi-70.0)<1e-9 or (abs(oi-50.0)<1e-9 and not bool(oi_meta.get('available'))):
        score-=10; penalties.append('open_interest_fallback')
    venues=int(signal.get('exchangeCount') or len(signal.get('marketExchanges') or []))
    if venues<=1: score-=15; penalties.append('single_venue')
    elif venues<3: score-=7; penalties.append('low_venue_coverage')
    if not signal.get('timeframes'): score-=12; penalties.append('missing_timeframes')
    if str(signal.get('structure1h') or 'N/A') in {'N/A','UNKNOWN',''}: score-=8; penalties.append('missing_structure')
    return {'score':round(_clamp(score),2),'penalties':penalties,'venues':venues,'missing_features':missing}

def _position_size(quality,positive_hits,passed):
    if not passed:return 0.0
    base=max(0.0,_env_float('POSITION_SIZE_BASE_USD',3.0))
    if not _env_bool('POSITION_SIZING_ENABLED',False): return base
    strong=max(base,_env_float('POSITION_SIZE_STRONG_USD',4.0)); mx=max(strong,_env_float('POSITION_SIZE_MAX_USD',5.0))
    return mx if quality>=86 and len(positive_hits)>=2 else (strong if quality>=78 else base)

def _setup_guard(signal):
    setup=str(signal.get('setup') or '').upper()
    d=str(signal.get('direction') or '').upper().replace('_BIAS',''); key=f'{setup}|{d}'
    st=_group_stat('setup_direction',key) or _group_stat('setup',setup); n=int(st.get('samples') or 0); ex_n=int(st.get('execution_samples') or 0)
    min_total=max(30,_env_int('SETUP_GUARD_MIN_PROFILE_SAMPLES',50)); min_ex=max(12,_env_int('SETUP_GUARD_MIN_EXECUTION_SAMPLES',20))
    if n<min_total:return {'blocked':False,'samples':n,'execution_samples':ex_n,'reason':'insufficient_profile'}
    if ex_n>=min_ex and st.get('execution_robust_avg_return') is not None:
        ret=float(st.get('execution_robust_avg_return') or 0); pf=float(st.get('execution_robust_profit_factor') or 0)
        blocked=_env_bool('SETUP_SHADOW_WHEN_UNPROFITABLE',True) and (ret<0 or pf<_env_float('SETUP_MIN_EXECUTION_PF',0.90))
        return {'blocked':blocked,'samples':n,'execution_samples':ex_n,'edge':round(_edge_from_stat(st),4),'stats':st,'reason':'negative_execution_specialist' if blocked else 'ok'}
    edge=_edge_from_stat(st); blocked=(setup=='BREAKOUT' and _env_bool('BREAKOUT_SHADOW_WHEN_UNPROFITABLE',True) and edge<0)
    return {'blocked':blocked,'samples':n,'execution_samples':ex_n,'edge':round(edge,4),'stats':st,'reason':'negative_specialist_expectancy' if blocked else 'ok'}

def _breakout_guard(signal):
    # Backwards-compatible diagnostic alias.
    return _setup_guard(signal) if str(signal.get('setup') or '').upper()=='BREAKOUT' else {'blocked':False}

def evaluate_signal(signal:Dict[str,Any])->Dict[str,Any]:
    p=_profile(); profile_valid=bool(p.get('valid')); overall=p.get('overall') or {}; prior=float(overall.get('win_rate',50.0)) if profile_valid else 50.0
    # Execution evidence must shrink toward an execution prior, never toward the
    # mark-to-market population prior. This fixes the V53 +14pp overconfidence bug.
    execution_prior=float(overall.get('execution_win_rate')) if profile_valid and overall.get('execution_win_rate') is not None else prior
    live_prob=float(signal.get('probability') or signal.get('aiProbability') or 50.0); direction=str(signal.get('direction') or '').upper().replace('_BIAS',''); setup=str(signal.get('setup') or 'NONE').upper(); t=signal.get('timeframes') or {}
    # Direction/setup specialists first. Generic contexts are intentionally excluded when their
    # specialist equivalent is available to avoid double-counting the same evidence.
    contexts=[('setup_direction',f'{setup}|{direction}'),('regime_direction',f"{signal.get('marketRegime') or signal.get('aiRegime')}|{direction}"),
              ('structure1h_direction',f"{signal.get('structure1h')}|{direction}"),('tf4h_direction',f"{t.get('4h')}|{direction}"),('symbol_direction',f"{signal.get('symbol')}|{direction}")]
    hist=[]; evidence=[]; edge_terms=[]
    if profile_valid:
        for group,key in contexts:
            st=_group_stat(group,key); n=int(st.get('samples') or 0); required=max(50,_env_int('PROFILE_SYMBOL_MIN_SAMPLES',50)) if group.startswith('symbol') else max(20,_env_int('PROFILE_DIRECTION_GROUP_MIN_SAMPLES',30))
            if n<required:continue
            ex_n=int(st.get('execution_samples') or 0); observed_rate=float(st.get('win_rate') or prior); strength=70 if group.startswith('symbol') else 45
            if ex_n>=max(12,_env_int('PROFILE_MIN_EXECUTION_CONTEXT_SAMPLES',20)) and st.get('execution_win_rate') is not None:
                observed_rate=float(st.get('execution_win_rate')); strength=max(12,strength//3); rate=_bayes_rate(execution_prior,observed_rate,ex_n,strength)
            else:
                rate=_bayes_rate(prior,observed_rate,n,strength)
            rate,recent=_blend_recent_rate(rate,group,key); weight=min(1.0,math.log1p(n)/6.0)
            hist.append((rate,weight)); edge=_edge_from_stat(st); edge_terms.append((edge,weight)); item={'context':f'{group}:{key}','samples':n,'win_rate':st.get('win_rate'),'robust_avg_return':st.get('robust_avg_return',st.get('avg_return')),'robust_profit_factor':st.get('robust_profit_factor',st.get('profit_factor')),'edge':round(edge,4)}
            if recent:item['recent']=recent
            evidence.append(item)
    hist_prob=sum(r*w for r,w in hist)/sum(w for _,w in hist) if hist else prior
    hist_edge=sum(e*w for e,w in edge_terms)/sum(w for _,w in edge_terms) if edge_terms else 0.0
    health=_profile_health(); hist_weight=_env_float('HEDGE_HISTORY_BLEND_WEIGHT',0.20) if profile_valid else 0.0
    if health.get('degraded'):hist_weight*=0.35
    hist_weight=max(0.0,min(0.45,hist_weight)); calibrated=(1-hist_weight)*live_prob+hist_weight*hist_prob
    hits=_rule_hits(signal) if profile_valid else []; adjustment=sum(float(h['adjustment']) for h in hits); cap=_env_float('HEDGE_MAX_RULE_ADJUSTMENT',12.0); adjustment=max(-cap,min(cap,adjustment)); calibrated=_clamp(calibrated+adjustment*0.30,2,92)
    execution=_execution_calibration(signal)
    if execution.get('available'):
        n=int(execution.get('samples') or 0); maxw=max(0,min(0.55,_env_float('EXECUTION_CALIBRATION_MAX_WEIGHT',0.30))); w=min(maxw,n/(n+120.0)); calibrated=(1-w)*calibrated+w*float(execution.get('probability') or calibrated)
    execution_ml={'available':False}
    try:
        from execution_model_v56 import predict as execution_ml_predict
        execution_ml=execution_ml_predict(signal)
        if execution_ml.get('available'):
            mlp=_clamp(execution_ml.get('profitProbability'),2,92); mean_auc=float(execution_ml.get('meanAuc') or 0.5)
            # V56: trust grows only from untouched OOS quality, never from model count.
            max_blend=max(0.0,min(0.50,_env_float('EXECUTION_ML_MAX_BLEND_WEIGHT',0.35)))
            if mean_auc < 0.56: mlw=0.0
            elif mean_auc < 0.58: mlw=min(max_blend,0.10)
            elif mean_auc < 0.61: mlw=min(max_blend,0.20)
            elif mean_auc < 0.65: mlw=min(max_blend,0.35)
            else: mlw=max_blend
            calibrated=(1-mlw)*calibrated+mlw*mlp
    except Exception: execution_ml={'available':False}
    reliability=_reliability(signal); rel=float(reliability['score'])/100.0
    # Incomplete signals are pulled toward uncertainty rather than being rewarded by fallback values.
    calibrated=50.0+(calibrated-50.0)*(0.55+0.45*rel); calibrated=_clamp(calibrated,2,92)
    rr=max(0.01,float(signal.get('rr') or 0)); entry=float(signal.get('entryPrice') or 0); stop=float(signal.get('stop') or 0); tp=float(signal.get('tp1') or 0)
    if entry>0 and stop>0 and tp>0:
        win_pct=abs(tp-entry)/entry*100; loss_pct=abs(entry-stop)/entry*100
    else:
        loss_pct=_env_float('HEDGE_DEFAULT_RISK_PCT',1.0); win_pct=loss_pct*rr
    pwin=calibrated/100.0; ev=pwin*win_pct-(1-pwin)*loss_pct
    uncertainty=float(signal.get('uncertainty') or signal.get('aiUncertainty') or 50)
    if execution_ml.get('available'): uncertainty=max(uncertainty,float(execution_ml.get('uncertainty') or 0))
    uncertainty_score=_clamp(100-uncertainty)
    historical_utility=_clamp(50+hist_edge*35)
    # V56 joint execution utility: a profitable idea that is unlikely to fill is not a high-quality executable signal.
    fill_score=_clamp(execution_ml.get('fillProbability'),0,100) if execution_ml.get('available') else _env_float('EXECUTION_EMPIRICAL_FILL_FALLBACK',70.0)
    executable_probability=calibrated*(fill_score/100.0)
    quality=_clamp(0.45*executable_probability+0.15*_clamp(float(signal.get('aiScore') or signal.get('score') or 0))+0.15*historical_utility+0.10*float(reliability['score'])+0.10*uncertainty_score+0.05*fill_score)
    adaptive={'available':False}
    try:
        from adaptive_model_runtime import predict as adaptive_predict
        adaptive=adaptive_predict(signal,quality,calibrated,ev)
        if adaptive.get('available'):
            aw=max(0,min(0.25,_env_float('ADAPTIVE_MODEL_BLEND_WEIGHT',0.10))); ap=_clamp(adaptive.get('probability'),2,92); calibrated=(1-aw)*calibrated+aw*ap; pwin=calibrated/100.0; ev=pwin*win_pct-(1-pwin)*loss_pct
            fill_score=_clamp(execution_ml.get('fillProbability'),0,100) if execution_ml.get('available') else 70.0
            executable_probability=calibrated*(fill_score/100.0); quality=_clamp(0.45*executable_probability+0.15*_clamp(float(signal.get('aiScore') or signal.get('score') or 0))+0.15*historical_utility+0.10*float(reliability['score'])+0.10*uncertainty_score+0.05*fill_score)
    except Exception:adaptive={'available':False}
    # Approximate interval around calibrated probability; broad when evidence/reliability is weak.
    n_eff=sum(w*min(200,int(item['samples'])) for (_,w),item in zip(hist,evidence)) if hist and evidence else 20.0
    if execution.get('available'):n_eff+=min(300,int(execution.get('samples') or 0))
    n_eff=max(12.0,n_eff*max(0.35,rel)); se=math.sqrt(max(1e-6,pwin*(1-pwin))/n_eff); margin=min(25.0,1.96*se*100.0+uncertainty*0.08)
    interval=[round(_clamp(calibrated-margin),2),round(_clamp(calibrated+margin),2)]
    setup_guard=_setup_guard(signal); breakout=_breakout_guard(signal); hard=[h for h in hits if h.get('hard_block')]
    min_quality=_env_float('HEDGE_MIN_QUALITY',62); min_ev=_env_float('HEDGE_MIN_EV_PCT',0.5); min_rel=_env_float('HEDGE_MIN_RELIABILITY',70); min_prob=_env_float('TRADE_MIN_PROBABILITY',60); min_rr=_env_float('TRADE_MIN_RR',2.0); min_fill=_env_float('EXECUTION_ML_MIN_FILL_PROBABILITY',50); min_joint=_env_float('EXECUTION_MIN_JOINT_PROBABILITY',35)
    if health.get('degraded'):
        min_quality+=_env_float('DEGRADED_QUALITY_BONUS',5); min_prob+=_env_float('DEGRADED_PROBABILITY_BONUS',3); min_ev+=_env_float('DEGRADED_EV_BONUS',0.5)
    ml_fill_ok=(not execution_ml.get('available')) or float(execution_ml.get('fillProbability') or 0)>=min_fill
    ml_return_ok=(not execution_ml.get('available')) or execution_ml.get('expectedReturnPct') is None or float(execution_ml.get('expectedReturnPct'))-float(execution_ml.get('uncertainty') or 0)*_env_float('EXECUTION_RETURN_UNCERTAINTY_PENALTY',0.01)>=_env_float('EXECUTION_ML_MIN_EXPECTED_RETURN_PCT',0.10)
    joint_ok=(not execution_ml.get('available')) or executable_probability>=min_joint
    passed=(not hard) and (not setup_guard.get('blocked')) and ml_fill_ok and ml_return_ok and joint_ok and quality>=min_quality and ev>=min_ev and reliability['score']>=min_rel and calibrated>=min_prob and rr>=min_rr
    if health.get('severe') and _env_bool('PROFILE_SEVERE_DEGRADATION_SHADOW_ONLY',True):
        # V55 recovery protocol: remain fail-closed by default, but a validated execution
        # champion may admit a deterministic tiny Paper canary to prove recovery live.
        canary=False
        if _env_bool('EXECUTION_CANARY_RECOVERY_ENABLED',True) and execution_ml.get('available'):
            auc=float(execution_ml.get('meanAuc') or 0); exret=execution_ml.get('expectedReturnPct'); fill=float(execution_ml.get('fillProbability') or 0)
            if auc>=_env_float('EXECUTION_CANARY_MIN_AUC',0.58) and (exret is None or float(exret)>=_env_float('EXECUTION_CANARY_MIN_RETURN_PCT',0.10)) and fill>=_env_float('EXECUTION_CANARY_MIN_FILL',55):
                import hashlib
                fp=str(signal.get('fingerprint') or signal.get('symbol') or '')
                bucket=int(hashlib.sha256(fp.encode()).hexdigest()[:8],16)%10000/100.0
                canary=bucket < _env_float('EXECUTION_CANARY_PERCENT',5.0)
        passed=bool(passed and canary)
    decision='HIGH_QUALITY' if passed and quality>=82 else ('TRADE_CANDIDATE' if passed else 'NO_TRADE'); positive=[h['name'] for h in hits if h['adjustment']>0]
    return {'hedgeProfileVersion':p.get('version','fallback'),'profileValid':profile_valid,'profileValidationReasons':p.get('validation_reasons') or [],
      'historicalProbability':round(hist_prob,2),'historicalUtilityScore':round(historical_utility,2),'calibratedProbability':round(calibrated,2),'executableProbability':round(executable_probability,2),'probabilityInterval95':interval,
      'expectedValuePct':round(ev,4),'expectedWinPct':round(win_pct,4),'expectedLossPct':round(loss_pct,4),'qualityScore':round(quality,2),'qualityDecision':decision,'qualityPassed':passed,
      'qualityAdjustment':round(adjustment,2),'qualityRules':hits,'historicalEvidence':evidence[:6],'antiProfileHits':[h['name'] for h in hits if h['adjustment']<0],
      'positiveProfileHits':positive,'suggestedPositionSizeUsd':round(_position_size(quality,positive,passed),2),'recencyEnabled':_env_bool('PROFILE_RECENCY_ENABLED',True),
      'adaptiveModelAvailable':bool(adaptive.get('available')),'adaptiveModelVersion':adaptive.get('version'),'adaptiveModelProbability':adaptive.get('probability'),
      'executionCalibration':execution,'executionModelV55':execution_ml,'reliability':reliability,'profileHealth':health,'setupGuard':setup_guard,'breakoutGuard':breakout,
      'effectiveThresholds':{'quality':round(min_quality,2),'probability':round(min_prob,2),'ev':round(min_ev,3),'rr':round(min_rr,2),'reliability':round(min_rel,2),'fill_probability':round(min_fill,2),'joint_probability':round(min_joint,2)}}
