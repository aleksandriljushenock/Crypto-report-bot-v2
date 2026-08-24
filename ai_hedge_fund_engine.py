"""Profit-oriented ensemble gate for trading signals.

Lightweight by design: no pandas/sklearn at runtime. The bundled profile is
precomputed from durable Supabase observations and combined with live factors.
"""
from __future__ import annotations
import json, math, os
from core.runtime_config import boolean, integer, number, string
from pathlib import Path
from typing import Any, Dict, Optional

PROFILE_PATH=Path(string('PROFIT_PROFILE_PATH','data/profit_profile_v2.json', strategy=False))
_CACHE=None


def _env_float(name: str, default: float) -> float:
    try:
        return number(name, float(default))
    except (TypeError, ValueError):
        return float(default)

def _env_int(name: str, default: int) -> int:
    try:
        return integer(name, int(default))
    except (TypeError, ValueError):
        return int(default)

def _env_bool(name: str, default: bool = False) -> bool:
    # Use the central runtime-config parser so an absent variable preserves the
    # requested default and Telegram/Supabase overrides remain effective.
    return boolean(name, default)

def _rule_weight(env_name: str, default: float) -> float:
    return _env_float(env_name, default)

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
    def add(name,adj,hard=False):
        if adj:
            hits.append({'name':name,'adjustment':round(float(adj),3),'hard_block':hard})

    # All empirical rule weights are configurable through Render ENV.
    if setup=='PULLBACK':
        add('pullback_profile', _rule_weight('RULE_WEIGHT_PULLBACK', 5))
    if setup=='BREAKOUT':
        add('breakout_base_penalty', _rule_weight('RULE_WEIGHT_BREAKOUT', -5))
    if vals['capital_flow']>=62 and vals['alignment']>=75 and vals['volume']>=65:
        add('flow_alignment_volume', _rule_weight('RULE_WEIGHT_FLOW_ALIGNMENT_VOLUME', 5))
    if vals['smart_money']>=60 and setup=='PULLBACK' and vals['volume']>=65:
        add('smart_pullback_volume', _rule_weight('RULE_WEIGHT_SMART_PULLBACK_VOLUME', 2))
    if vals['smart_money']>=60 and setup=='PULLBACK' and prob>=70:
        add('smart_pullback_probability', _rule_weight('RULE_WEIGHT_SMART_PULLBACK_PROBABILITY', 3))
    if prob>=72 and vals['trend']>=85 and qv>=130_000_000:
        add('probability_trend_liquidity', _rule_weight('RULE_WEIGHT_PROBABILITY_TREND_LIQUIDITY', 10))
    if t.get('1d')=='UP' and t.get('5m')=='UP' and vals['alignment']>=75:
        add('daily_micro_alignment', _rule_weight('RULE_WEIGHT_DAILY_MICRO_ALIGNMENT', 8))
    if str(s.get('structure1h')) in ('SWEEP_HIGH','BOS_UP'):
        add('structure_1h_confirmed', _rule_weight('RULE_WEIGHT_STRUCTURE_1H', 4))

    # v22 cross-exchange confirmation. Coverage is supportive, never a stand-alone signal.
    venues = int(s.get('exchangeCount') or len(s.get('marketExchanges') or []))
    if venues >= 5:
        add('cross_exchange_coverage_5', _rule_weight('RULE_WEIGHT_CROSS_EXCHANGE_5', 3))
    elif venues >= 3:
        add('cross_exchange_coverage_3', _rule_weight('RULE_WEIGHT_CROSS_EXCHANGE_3', 1.5))
    move = float(s.get('crossExchangeChangeMedian') or 0)
    if str(s.get('direction')) == 'LONG_BIAS' and move >= 3:
        add('cross_exchange_momentum_long', _rule_weight('RULE_WEIGHT_CROSS_EXCHANGE_MOMENTUM', 2))
    elif str(s.get('direction')) == 'SHORT_BIAS' and move <= -3:
        add('cross_exchange_momentum_short', _rule_weight('RULE_WEIGHT_CROSS_EXCHANGE_MOMENTUM', 2))

    # Negative profiles remain configurable too, but defaults preserve the tested behavior.
    if qv<130_000_000:
        add('low_liquidity', _rule_weight('RULE_WEIGHT_LOW_LIQUIDITY', -8), qv<80_000_000)
    if vals['capital_flow']<=50:
        add('weak_capital_flow', _rule_weight('RULE_WEIGHT_WEAK_CAPITAL_FLOW', -8), vals['capital_flow']<42)
    if conf<36:
        add('low_confidence', _rule_weight('RULE_WEIGHT_LOW_CONFIDENCE', -7), conf<30)
    if unc>64:
        add('high_uncertainty', _rule_weight('RULE_WEIGHT_HIGH_UNCERTAINTY', -7), unc>72)
    if s.get('direction')=='LONG_BIAS' and t.get('4h')=='DOWN':
        add('long_against_4h', _rule_weight('RULE_WEIGHT_LONG_AGAINST_4H', -12), True)
    if setup=='BREAKOUT' and vals['volume']<60:
        add('weak_breakout', _rule_weight('RULE_WEIGHT_WEAK_BREAKOUT', -8))
    if setup=='BREAKOUT' and t.get('5m') in ('DOWN','RANGE'):
        add('breakout_micro_weak', _rule_weight('RULE_WEIGHT_BREAKOUT_MICRO_WEAK', -6))
    return hits

def _recent_group_stat(group: str, key: Any) -> tuple[Dict[str, Any], Optional[int]]:
    p = _profile()
    requested = max(1, _env_int('PROFILE_RECENT_WINDOW_DAYS', 21))
    windows = p.get('recent_windows') or {}
    if windows:
        available = []
        for raw in windows:
            try:
                available.append(int(raw))
            except Exception:
                continue
        if available:
            selected = min(available, key=lambda days: abs(days - requested))
            groups = windows.get(str(selected)) or windows.get(selected) or {}
            return ((groups.get(group) or {}).get(str(key)) or {}), selected
    # Backward compatibility with one-window V40 profiles.
    recent_groups = p.get('recent_groups') or {}
    meta_days = p.get('recent_window_days')
    try:
        meta_days = int(meta_days) if meta_days is not None else None
    except Exception:
        meta_days = None
    return ((recent_groups.get(group) or {}).get(str(key)) or {}), meta_days

def _blend_recent_rate(base_rate: float, group: str, key: Any) -> tuple[float, Optional[Dict[str, Any]]]:
    """Blend long-history and recent statistics using runtime recency controls.

    V41 profiles carry several recent windows, so PROFILE_RECENT_WINDOW_DAYS is a
    true runtime selector. PROFILE_HALF_LIFE_DAYS controls how quickly the chosen
    recent window loses influence as it gets older/wider.
    """
    if not _env_bool('PROFILE_RECENCY_ENABLED', True):
        return base_rate, None
    st, selected_window = _recent_group_stat(group, key)
    n = int(st.get('samples') or 0)
    min_n = _env_int('PROFILE_MIN_RECENT_SAMPLES', 30)
    if n < min_n:
        return base_rate, None
    recent_rate = float(st.get('win_rate') or base_rate)
    configured_weight = max(0.0, _env_float('PROFILE_RECENT_WEIGHT', 2.0))
    half_life = max(1.0, _env_float('PROFILE_HALF_LIFE_DAYS', 14.0))
    window = float(selected_window or _env_int('PROFILE_RECENT_WINDOW_DAYS', 21))
    decay = 0.5 ** (max(0.0, window) / half_life)
    recent_weight = configured_weight * decay
    if recent_weight <= 0:
        return base_rate, None
    blended = (base_rate + recent_rate * recent_weight) / (1.0 + recent_weight)
    return blended, {
        'samples': n, 'win_rate': recent_rate, 'weight': round(recent_weight, 4),
        'configured_weight': configured_weight, 'window_days': selected_window,
        'half_life_days': half_life, 'decay': round(decay, 4),
    }

def _position_size(quality: float, positive_hits: list[str], passed: bool) -> float:
    if not _env_bool('POSITION_SIZING_ENABLED', False) or not passed:
        return 0.0 if not passed else _env_float('POSITION_SIZE_BASE_USD', 3.0)
    base = max(0.0, _env_float('POSITION_SIZE_BASE_USD', 3.0))
    strong = max(base, _env_float('POSITION_SIZE_STRONG_USD', 4.0))
    maximum = max(strong, _env_float('POSITION_SIZE_MAX_USD', 5.0))
    strong_rules = {'probability_trend_liquidity', 'daily_micro_alignment'}
    count = len(strong_rules.intersection(set(positive_hits)))
    if count >= 2 and quality >= 78:
        return maximum
    if count >= 1 or quality >= 80:
        return strong
    return base

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
            rate, recent = _blend_recent_rate(rate, group, key)
            weight=min(1.0,math.log1p(n)/5.0)
            hist_rates.append((rate,weight))
            item={'context':f'{group}:{key}','samples':n,'win_rate':st.get('win_rate'),'avg_return':st.get('avg_return')}
            if recent:
                item['recent'] = recent
            evidence.append(item)
    hist_prob=sum(r*w for r,w in hist_rates)/sum(w for _,w in hist_rates) if hist_rates else prior
    # Ensemble: local model + historical context. Chronos contribution is already in live probability when enabled.
    calibrated=0.58*live_prob+0.42*hist_prob
    hits=_rule_hits(signal); adjustment=sum(h['adjustment'] for h in hits)
    cap=number('HEDGE_MAX_RULE_ADJUSTMENT',24.0); adjustment=max(-cap,min(cap,adjustment))
    calibrated=_clamp(calibrated+adjustment*0.45,2,92)
    rr=max(0.01,float(signal.get('rr') or 0))
    # Prefer actual TP/SL geometry when available, fallback to RR-normalized payoffs.
    entry=float(signal.get('entryPrice') or 0); stop=float(signal.get('stop') or 0); tp=float(signal.get('tp1') or 0)
    if entry>0 and stop>0 and tp>0:
        direction=signal.get('direction')
        win_pct=abs(tp-entry)/entry*100; loss_pct=abs(entry-stop)/entry*100
    else:
        loss_pct=number('HEDGE_DEFAULT_RISK_PCT',1.0); win_pct=loss_pct*rr
    pwin=calibrated/100.0
    ev=pwin*win_pct-(1-pwin)*loss_pct
    # Robustness penalty for concentration/unknowns and hard blocks.
    hard=[h for h in hits if h['hard_block']]
    quality=_clamp(0.58*calibrated+0.22*_clamp(float(signal.get('aiScore') or signal.get('score') or 0))+0.20*_clamp(50+ev*8))

    # v18 adaptive champion: blend only after the normal deterministic model has
    # produced a complete feature vector. This preserves the existing strategy
    # as the majority vote and makes the adaptive layer safe to disable instantly.
    adaptive={'available':False}
    try:
        from adaptive_model_runtime import predict as adaptive_predict
        adaptive=adaptive_predict(signal, quality, calibrated, ev)
        if adaptive.get('available'):
            adaptive_weight=max(0.0,min(0.45,_env_float('ADAPTIVE_MODEL_BLEND_WEIGHT',0.20)))
            adaptive_prob=_clamp(adaptive.get('probability'),2,92)
            calibrated=(1.0-adaptive_weight)*calibrated+adaptive_weight*adaptive_prob
            pwin=calibrated/100.0
            ev=pwin*win_pct-(1-pwin)*loss_pct
            quality=_clamp(0.58*calibrated+0.22*_clamp(float(signal.get('aiScore') or signal.get('score') or 0))+0.20*_clamp(50+ev*8))
    except Exception:
        adaptive={'available':False}

    min_quality=number('HEDGE_MIN_QUALITY',70.0)
    min_ev=number('HEDGE_MIN_EV_PCT',2.0)
    passed=(not hard) and quality>=min_quality and ev>=min_ev
    decision='HIGH_QUALITY' if passed and quality>=80 else ('TRADE_CANDIDATE' if passed else 'NO_TRADE')
    positive_hits=[h['name'] for h in hits if h['adjustment']>0]
    suggested_size=_position_size(quality, positive_hits, passed)
    return {
      'hedgeProfileVersion':p.get('version','fallback'),'historicalProbability':round(hist_prob,2),
      'calibratedProbability':round(calibrated,2),'expectedValuePct':round(ev,4),
      'expectedWinPct':round(win_pct,4),'expectedLossPct':round(loss_pct,4),
      'qualityScore':round(quality,2),'qualityDecision':decision,'qualityPassed':passed,
      'qualityAdjustment':round(adjustment,2),'qualityRules':hits,'historicalEvidence':evidence[:6],
      'antiProfileHits':[h['name'] for h in hits if h['adjustment']<0],
      'positiveProfileHits':positive_hits,
      'suggestedPositionSizeUsd':round(suggested_size,2),
      'recencyEnabled':_env_bool('PROFILE_RECENCY_ENABLED',True),
      'adaptiveModelAvailable':bool(adaptive.get('available')),
      'adaptiveModelVersion':adaptive.get('version'),
      'adaptiveModelProbability':adaptive.get('probability'),
    }
