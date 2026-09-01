"""V55 execution intelligence feature extraction.

Uses only values known at decision time. Supports both complete signal snapshots and
legacy rows, but exposes explicit missingness so neutral fallbacks are not mistaken
for real evidence.
"""
from __future__ import annotations
import json, math
from typing import Any, Dict

BASE_FEATURES=(
 'score','raw_probability','final_probability','quality','ev','rr','confidence','uncertainty','quote_volume_log',
 'trend','momentum','volume','volatility','sentiment','funding','open_interest','liquidations','alignment','risk_reward',
 'capital_flow','smart_money','narrative','coverage','cross_change','is_short','is_breakout','is_pullback',
 'regime_bull','regime_bear','regime_range','tf1d_up','tf1d_down','tf4h_up','tf4h_down','tf1h_up','tf1h_down',
 'tf15m_up','tf15m_down','tf5m_up','tf5m_down','feature_coverage','fallback_fraction','decision_accepted')
MISSING_FEATURES=tuple('missing_'+x for x in ('trend','momentum','volume','volatility','sentiment','funding','open_interest','liquidations','alignment','risk_reward','capital_flow','smart_money','narrative'))
FEATURE_NAMES=BASE_FEATURES+MISSING_FEATURES


def _num(v:Any,d:float=0.0)->float:
    try:return float(v)
    except Exception:return float(d)

def _dict(v:Any)->Dict[str,Any]:
    if isinstance(v,dict):return v
    if isinstance(v,str):
        try:
            x=json.loads(v); return x if isinstance(x,dict) else {}
        except Exception:return {}
    return {}

def payload(obj:Dict[str,Any])->Dict[str,Any]:
    return _dict(obj.get('feature_payload') or obj.get('payload') or obj.get('signal_payload') or obj)

def _present(f:Dict[str,Any], key:str)->bool:
    if key not in f or f.get(key) is None:return False
    v=_num(f.get(key),50)
    # Legacy pipelines encoded missing feeds as neutral sentinels. Do not treat those as evidence.
    if key=='open_interest' and abs(v-70.0)<1e-9:return False
    if key in {'capital_flow','smart_money','narrative','sentiment'} and abs(v-50.0)<1e-9:return False
    return True

def extract(obj:Dict[str,Any])->list[float]:
    p=payload(obj); f=_dict(p.get('aiFactors') or p.get('features') or p.get('tradeProfile'))
    t=_dict(p.get('timeframes'))
    ds=_dict(p.get('decisionSnapshot'))
    direction=str(obj.get('direction') or obj.get('side') or p.get('direction') or p.get('signal_direction') or '').upper()
    setup=str(obj.get('setup') or p.get('setup') or '').upper(); regime=str(p.get('marketRegime') or p.get('aiRegime') or p.get('regime') or '').lower()
    venues=p.get('marketExchanges') or p.get('venues') or p.get('exchangeCoverage') or []
    if isinstance(venues,list):coverage=float(len(venues))
    elif isinstance(venues,dict):coverage=_num(venues.get('count'),1)
    else:coverage=_num(p.get('exchangeCount') or p.get('exchangeCoverageCount'),1)
    qv=max(0.0,_num(p.get('quoteVolume') or p.get('quote_volume')))
    factor_keys=('trend','momentum','volume','volatility','sentiment','funding','open_interest','liquidations','alignment','risk_reward','capital_flow','smart_money','narrative')
    present={k:_present(f,k) for k in factor_keys}; vals={k:_num(f.get(k),50) for k in factor_keys}
    coverage_ratio=sum(present.values())/len(factor_keys)
    fallback_fraction=1.0-coverage_ratio
    def tf(name,state):return 1.0 if str(t.get(name) or '').upper()==state else 0.0
    raw_prob=_num(ds.get('rawProbability') if ds else (p.get('probability') or obj.get('probability')),50)
    final_prob=_num(ds.get('finalProbability') if ds else (p.get('finalProbability') or p.get('calibratedProbability') or p.get('probability') or obj.get('probability')),50)
    accepted=1.0 if str(p.get('decisionAtSignal') or obj.get('decision_at_signal') or '').upper() in {'ACCEPTED','TRADE_CANDIDATE','PAPER'} else 0.0
    base=[
      _num(p.get('aiScore') or p.get('score') or obj.get('score'),50),raw_prob,final_prob,
      _num(p.get('qualityScore') or p.get('quality') or obj.get('quality'),50),_num(p.get('expectedValuePct') or p.get('ev') or obj.get('ev'),0),_num(p.get('rr'),1),
      _num(p.get('confidence') or p.get('signal_confidence'),50),_num(p.get('uncertainty') or p.get('aiUncertainty'),50),math.log1p(qv),
      vals['trend'],vals['momentum'],vals['volume'],vals['volatility'],vals['sentiment'],vals['funding'],vals['open_interest'],vals['liquidations'],vals['alignment'],vals['risk_reward'],vals['capital_flow'],vals['smart_money'],vals['narrative'],coverage,_num(p.get('crossExchangeChangeMedian'),0),
      1.0 if 'SHORT' in direction else 0.0,1.0 if setup=='BREAKOUT' else 0.0,1.0 if setup=='PULLBACK' else 0.0,
      1.0 if 'bull' in regime else 0.0,1.0 if 'bear' in regime else 0.0,1.0 if 'range' in regime else 0.0,
      tf('1d','UP'),tf('1d','DOWN'),tf('4h','UP'),tf('4h','DOWN'),tf('1h','UP'),tf('1h','DOWN'),tf('15m','UP'),tf('15m','DOWN'),tf('5m','UP'),tf('5m','DOWN'),
      coverage_ratio,fallback_fraction,accepted]
    return base+[0.0 if present[k] else 1.0 for k in factor_keys]

def specialist_key(obj:Dict[str,Any])->str:
    p=payload(obj); direction=str(obj.get('direction') or obj.get('side') or p.get('direction') or '').upper().replace('_BIAS',''); setup=str(obj.get('setup') or p.get('setup') or 'NONE').upper()
    return f'{setup}|{direction}'
