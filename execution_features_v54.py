"""Shared feature extraction for V54 execution/fill models.

Only values available at signal time are used. No outcome or future-price fields are
accepted here, which keeps training/runtime schemas aligned and prevents leakage.
"""
from __future__ import annotations
from typing import Any, Dict

FEATURE_NAMES = (
    'score','probability','quality','ev','rr','confidence','uncertainty','quote_volume_log',
    'trend','momentum','volume','volatility','sentiment','funding','open_interest','liquidations',
    'alignment','risk_reward','capital_flow','smart_money','narrative','coverage','cross_change',
    'is_short','is_breakout','is_pullback','regime_bull','regime_bear','regime_range',
    'tf1d_up','tf1d_down','tf4h_up','tf4h_down','tf1h_up','tf1h_down','tf15m_up','tf15m_down','tf5m_up','tf5m_down',
)

def _num(v: Any, d: float=0.0) -> float:
    try: return float(v)
    except Exception: return float(d)

def _payload(obj: Dict[str,Any]) -> Dict[str,Any]:
    p=obj.get('feature_payload') or obj.get('payload') or obj.get('signal_payload') or obj
    if isinstance(p,str):
        import json
        try:p=json.loads(p)
        except Exception:p={}
    return p if isinstance(p,dict) else {}

def extract(obj: Dict[str,Any]) -> list[float]:
    p=_payload(obj); f=p.get('aiFactors') or p.get('features') or {}
    if isinstance(f,str):
        import json
        try:f=json.loads(f)
        except Exception:f={}
    t=p.get('timeframes') or {}; direction=str(obj.get('direction') or obj.get('side') or p.get('direction') or p.get('signal_direction') or '').upper(); setup=str(obj.get('setup') or p.get('setup') or '').upper(); regime=str(p.get('marketRegime') or p.get('aiRegime') or p.get('regime') or '').lower()
    venues=p.get('marketExchanges') or p.get('venues') or p.get('exchangeCoverage') or []
    if isinstance(venues,list): coverage=float(len(venues))
    elif isinstance(venues,dict): coverage=_num(venues.get('count'),1)
    else: coverage=_num(p.get('exchangeCount') or p.get('exchangeCoverageCount'),1)
    qv=max(0.0,_num(p.get('quoteVolume') or p.get('quote_volume')))
    import math
    vals={k:_num(f.get(k),50) for k in ('trend','momentum','volume','volatility','sentiment','funding','open_interest','liquidations','alignment','risk_reward','capital_flow','smart_money','narrative')}
    def tf(name,state): return 1.0 if str(t.get(name) or '').upper()==state else 0.0
    return [
      _num(p.get('aiScore') or p.get('score') or obj.get('score'),50),_num(p.get('calibratedProbability') or p.get('probability') or obj.get('probability'),50),
      _num(p.get('qualityScore') or p.get('quality') or obj.get('quality'),50),_num(p.get('expectedValuePct') or p.get('ev') or obj.get('ev'),0),_num(p.get('rr'),1),
      _num(p.get('confidence') or p.get('signal_confidence'),50),_num(p.get('uncertainty') or p.get('aiUncertainty'),50),math.log1p(qv),
      vals['trend'],vals['momentum'],vals['volume'],vals['volatility'],vals['sentiment'],vals['funding'],vals['open_interest'],vals['liquidations'],vals['alignment'],vals['risk_reward'],vals['capital_flow'],vals['smart_money'],vals['narrative'],coverage,_num(p.get('crossExchangeChangeMedian'),0),
      1.0 if 'SHORT' in direction else 0.0,1.0 if setup=='BREAKOUT' else 0.0,1.0 if setup=='PULLBACK' else 0.0,
      1.0 if 'bull' in regime else 0.0,1.0 if 'bear' in regime else 0.0,1.0 if 'range' in regime else 0.0,
      tf('1d','UP'),tf('1d','DOWN'),tf('4h','UP'),tf('4h','DOWN'),tf('1h','UP'),tf('1h','DOWN'),tf('15m','UP'),tf('15m','DOWN'),tf('5m','UP'),tf('5m','DOWN')]

def specialist_key(obj: Dict[str,Any]) -> str:
    p=_payload(obj); direction=str(obj.get('direction') or obj.get('side') or p.get('direction') or '').upper().replace('_BIAS',''); setup=str(obj.get('setup') or p.get('setup') or 'NONE').upper()
    return f'{setup}|{direction}'
