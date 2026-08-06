"""Profit-oriented ensemble gate for trading signals.

Lightweight by design: no pandas/sklearn at runtime. The bundled profile is
precomputed from durable Supabase observations and combined with live factors.
"""
from __future__ import annotations
import json, math, os
from pathlib import Path
from typing import Any, Dict

PROFILE_PATH=Path(os.getenv('PROFIT_PROFILE_PATH','data/profit_profile_v2.json'))
_CACHE=None

def _clamp(v,lo=0.0,hi=100.0):
    try:return max(lo,min(hi,float(v)))
    except:return lo

def _profile():
    global _CACHE
    if _CACHE is None:
        try:_CACHE=json.loads(PROFILE_PATH.read_text(encoding='utf-8'))
        except Exception:_CACHE={'overall':{'win_rate':40.0,'avg_win':10.0,'avg_loss':8.0},'groups':{},'rules':[]}
    return _CACHE

def _group_stat(group,key):
    return ((_profile().get('groups') or {}).get(group) or {}).get(str(key)) or {}

def _bayes_rate(prior_pct, wins_pct, n, strength=24.0):
    if not n:return prior_pct
    return (prior_pct*strength + wins_pct*n)/(strength+n)

def _rule_hits(s):
    f=s.get('aiFactors') or {}
    setup=str(s.get('setup') or 'NONE').upper(); t=s.get('timeframes') or {}
    qv=float(s.get('quoteVolume') or 0); prob=float(s.get('probability') or s.get('aiProbability') or 0)
    conf=float(s.get('confidence') or 0); unc=float(s.get('uncertainty') or s.get('aiUncertainty') or 100)
    vals={k:float(f.get(k,50) or 50) for k in ('trend','volume','alignment','capital_flow','smart_money')}
    hits=[]
    def add(name,adj,hard=False):hits.append({'name':name,'adjustment':adj,'hard_block':hard})
    if setup=='PULLBACK':add('pullback_profile',5)
    if setup=='BREAKOUT':add('breakout_base_penalty',-5)
    if vals['capital_flow']>=62 and vals['alignment']>=75 and vals['volume']>=65:add('flow_alignment_volume',9)
    if vals['smart_money']>=60 and setup=='PULLBACK' and vals['volume']>=65:add('smart_pullback_volume',7)
    if vals['smart_money']>=60 and setup=='PULLBACK' and prob>=70:add('smart_pullback_probability',6)
    if prob>=72 and vals['trend']>=85 and qv>=130_000_000:add('probability_trend_liquidity',6)
    if t.get('1d')=='UP' and t.get('5m')=='UP' and vals['alignment']>=75:add('daily_micro_alignment',5)
    if str(s.get('structure1h')) in ('SWEEP_HIGH','BOS_UP'):add('structure_1h_confirmed',4)
    if qv<130_000_000:add('low_liquidity',-8, qv<80_000_000)
    if vals['capital_flow']<=50:add('weak_capital_flow',-8, vals['capital_flow']<42)
    if conf<36:add('low_confidence',-7, conf<30)
    if unc>64:add('high_uncertainty',-7, unc>72)
    if s.get('direction')=='LONG_BIAS' and t.get('4h')=='DOWN':add('long_against_4h',-12,True)
    if setup=='BREAKOUT' and vals['volume']<60:add('weak_breakout',-8)
    if setup=='BREAKOUT' and t.get('5m') in ('DOWN','RANGE'):add('breakout_micro_weak',-6)
    return hits

def evaluate_signal(signal:Dict[str,Any])->Dict[str,Any]:
    p=_profile(); overall=p.get('overall') or {}
    prior=float(overall.get('win_rate',40.0)); factors=signal.get('aiFactors') or {}
    live_prob=float(signal.get('probability') or signal.get('aiProbability') or prior)
    evidence=[]
    # Blend historical context: setup, regime, structure, symbol. Shrink small groups.
    contexts=[('setup',signal.get('setup')),('regime',signal.get('marketRegime') or signal.get('aiRegime')),
              ('structure1h',signal.get('structure1h')),('tf4h',(signal.get('timeframes') or {}).get('4h')),
              ('symbol',signal.get('symbol'))]
    hist_rates=[]
    for group,key in contexts:
        st=_group_stat(group,key)
        n=int(st.get('samples') or 0)
        if n>=8:
            rate=_bayes_rate(prior,float(st.get('win_rate') or prior),n,28 if group=='symbol' else 18)
            weight=min(1.0,math.log1p(n)/5.0)
            hist_rates.append((rate,weight)); evidence.append({'context':f'{group}:{key}','samples':n,'win_rate':st.get('win_rate'),'avg_return':st.get('avg_return')})
    hist_prob=sum(r*w for r,w in hist_rates)/sum(w for _,w in hist_rates) if hist_rates else prior
    # Ensemble: local model + historical context. Chronos contribution is already in live probability when enabled.
    calibrated=0.58*live_prob+0.42*hist_prob
    hits=_rule_hits(signal); adjustment=sum(h['adjustment'] for h in hits)
    cap=float(os.getenv('HEDGE_MAX_RULE_ADJUSTMENT','24')); adjustment=max(-cap,min(cap,adjustment))
    calibrated=_clamp(calibrated+adjustment*0.45,2,92)
    rr=max(0.01,float(signal.get('rr') or 0))
    # Prefer actual TP/SL geometry when available, fallback to RR-normalized payoffs.
    entry=float(signal.get('entryPrice') or 0); stop=float(signal.get('stop') or 0); tp=float(signal.get('tp1') or 0)
    if entry>0 and stop>0 and tp>0:
        direction=signal.get('direction')
        win_pct=abs(tp-entry)/entry*100; loss_pct=abs(entry-stop)/entry*100
    else:
        loss_pct=float(os.getenv('HEDGE_DEFAULT_RISK_PCT','1.0')); win_pct=loss_pct*rr
    pwin=calibrated/100.0
    ev=pwin*win_pct-(1-pwin)*loss_pct
    # Robustness penalty for concentration/unknowns and hard blocks.
    hard=[h for h in hits if h['hard_block']]
    quality=_clamp(0.58*calibrated+0.22*_clamp(float(signal.get('aiScore') or signal.get('score') or 0))+0.20*_clamp(50+ev*8))
    min_quality=float(os.getenv('HEDGE_MIN_QUALITY','70'))
    min_ev=float(os.getenv('HEDGE_MIN_EV_PCT','0.20'))
    passed=(not hard) and quality>=min_quality and ev>=min_ev
    decision='HIGH_QUALITY' if passed and quality>=80 else ('TRADE_CANDIDATE' if passed else 'NO_TRADE')
    return {
      'hedgeProfileVersion':p.get('version','fallback'),'historicalProbability':round(hist_prob,2),
      'calibratedProbability':round(calibrated,2),'expectedValuePct':round(ev,4),
      'expectedWinPct':round(win_pct,4),'expectedLossPct':round(loss_pct,4),
      'qualityScore':round(quality,2),'qualityDecision':decision,'qualityPassed':passed,
      'qualityAdjustment':round(adjustment,2),'qualityRules':hits,'historicalEvidence':evidence[:6],
      'antiProfileHits':[h['name'] for h in hits if h['adjustment']<0],
      'positiveProfileHits':[h['name'] for h in hits if h['adjustment']>0],
    }
