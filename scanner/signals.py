"""Signal normalization and deterministic pre-AI gate."""
from __future__ import annotations
import hashlib
from core.runtime_config import number
from scanner.analysis import build_trade_profile

def _best_rr(row):
    rules = row['rules']
    if rules.get('bestSetup') == 'BREAKOUT':
        return rules.get('rrBreakout')
    if rules.get('bestSetup') == 'PULLBACK':
        return rules.get('rrPullback')
    return max([x for x in (rules.get('rrBreakout'), rules.get('rrPullback')) if x is not None] or [0])


def row_to_signal(row):
    score, levels, rules = row['score'], row.get('levels') or {}, row['rules']
    setup = rules.get('bestSetup') or 'NONE'
    if setup == 'BREAKOUT':
        entry_text = f"> {levels.get('breakoutEntry')}"
        stop = levels.get('breakoutStop')
    else:
        zone = levels.get('pullbackEntryZone') or []
        entry_text = f"{zone[0]}–{zone[1]}" if len(zone) == 2 else 'N/A'
        stop = levels.get('pullbackStop')
    rr = _best_rr(row)
    raw_fp = '|'.join([
        score.get('symbol', ''), score.get('direction', ''), setup,
        str(round(float(stop or 0), 6)), str(round(float(rr or 0), 2)),
    ])
    fingerprint = hashlib.sha256(raw_fp.encode('utf-8')).hexdigest()
    timeframe = row.get('timeframe') or {'labels': {}, 'alignment': 0}
    profile = build_trade_profile(score, rules, rr, timeframe)
    entry_price = levels.get('breakoutEntry') if setup == 'BREAKOUT' else (sum(levels.get('pullbackEntryZone') or []) / 2 if len(levels.get('pullbackEntryZone') or []) == 2 else None)
    return {
        'fingerprint': fingerprint,
        'symbol': score.get('symbol'),
        'timeframe': 'multi_tf',
        'primaryTimeframe': '15m',
        'direction': score.get('direction'),
        'status': rules.get('finalStatus'),
        'setup': setup,
        'score': score.get('score', 0),
        'probability': profile['probability'],
        'confidence': profile['confidence'],
        'tradeProfile': profile,
        'timeframes': timeframe.get('labels', {}),
        'alignment': timeframe.get('alignment', 0),
        'rr': rr,
        'entryPrice': entry_price,
        'entryText': entry_text,
        'stop': stop,
        'tp1': levels.get('tp1'),
        'tp2': levels.get('tp2'),
        'tp3': levels.get('tp3'),
        'mainReason': rules.get('mainReason'),
        'confirmations': rules.get('confirmations', [])[:6],
        'risks': (rules.get('reasonsToWatch') or rules.get('reasonsToSkip') or [])[:4],
        'quoteVolume': score.get('quoteVolume'),
        'fundingPercent': score.get('fundingPercent'),
        'takerRatio': score.get('takerBuySellRatio'),
        'structure15m': score.get('structure15m'),
        'structure1h': score.get('structure1h'),
        'listing': row.get('listing', {}),
        'marketExchanges': row.get('marketExchanges', []),
        'exchangeCount': row.get('exchangeCount', 0),
        'priceChange24h': score.get('priceChange24h'),
        'relativeVolume15m': score.get('relativeVolume15m'),
        'relativeVolume1h': score.get('relativeVolume1h'),
        'atr1hPercent': score.get('atr1hPercent'),
        'rsi1h': score.get('rsi1h'),
        'fastScanBucket': row.get('fastScanBucket'),
        'crossExchangeChangeMedian': row.get('crossExchangeChangeMedian'),
        # Kept only until final AI ranking; removed before persistence.
        '_chronosCloses': list(row.get('chronosCloses') or []),
    }


def _env_float(name, default):
    try:
        return number(name, default)
    except Exception:
        return float(default)


def _strategy_profile(signal):
    """Choose a profile without changing the executable setup geometry."""
    setup = str(signal.get('setup') or '').upper()
    change = abs(float(signal.get('priceChange24h') or 0))
    rv = max(float(signal.get('relativeVolume15m') or 0), float(signal.get('relativeVolume1h') or 0))
    # Momentum is a scoring profile; execution still uses the original breakout/pullback levels.
    if change >= _env_float('MOMENTUM_PROFILE_MIN_CHANGE_PCT', 4.0) and rv >= _env_float('MOMENTUM_PROFILE_MIN_REL_VOLUME', 1.25):
        return 'MOMENTUM'
    return 'PULLBACK' if setup == 'PULLBACK' else 'BREAKOUT'


def _profile_thresholds(profile, base_score, base_rr, base_probability):
    prefix = f'TRADE_{profile}_MIN_'
    # Global thresholds are hard safety floors. A profile may tighten them,
    # but must never silently weaken an operator-selected global minimum.
    return {
        'score': max(float(base_score), _env_float(prefix + 'SCORE', base_score)),
        'rr': max(float(base_rr), _env_float(prefix + 'RR', base_rr)),
        'probability': max(float(base_probability), _env_float(prefix + 'PROBABILITY', base_probability)),
    }


def find_trade_signals(rows, min_score=72, min_rr=2.0, include_watch=False, max_results=5, min_probability=65, diagnostics=None):
    signals = []
    diag = diagnostics if diagnostics is not None else {}
    diag.update({"analyzed": 0, "status": 0, "score": 0, "rr": 0, "probability": 0, "nearMisses": []})
    near = []
    for row in rows:
        if 'error' in row or not row.get('rules'):
            continue
        diag["analyzed"] += 1
        status = row['rules'].get('finalStatus')
        if status != 'TRADE_CANDIDATE' and not (include_watch and status == 'WATCH'):
            continue
        diag["status"] += 1
        signal = row_to_signal(row)
        profile = _strategy_profile(signal)
        signal['signalProfile'] = profile
        thresholds = _profile_thresholds(profile, min_score, min_rr, min_probability)
        threshold = thresholds['score'] if status == 'TRADE_CANDIDATE' else thresholds['score'] + 3
        signal['profileThresholds'] = thresholds
        reasons = []
        if float(signal.get('score') or 0) < threshold:
            reasons.append('Score')
        else:
            diag["score"] += 1
        if not reasons and float(signal.get('rr') or 0) < thresholds['rr']:
            reasons.append('R/R')
        elif not reasons:
            diag["rr"] += 1
        if not reasons and float(signal.get('probability') or 0) < thresholds['probability']:
            reasons.append('Probability')
        elif not reasons:
            diag["probability"] += 1
        if reasons:
            signal['reason'] = ', '.join(reasons)
            near.append(signal)
            continue
        signals.append(signal)
    signals.sort(key=lambda s: (s['status'] == 'TRADE_CANDIDATE', s['score'], s['rr']), reverse=True)
    near.sort(key=lambda s: (float(s.get('probability') or 0), float(s.get('score') or 0), float(s.get('rr') or 0)), reverse=True)
    diag["nearMisses"] = [{
        "symbol": x.get("symbol"), "reason": x.get("reason"), "score": x.get("score"),
        "rr": x.get("rr"), "probability": x.get("probability"),
        "direction": x.get("direction"), "setup": x.get("setup"),
        "entryPrice": x.get("entryPrice"), "entryText": x.get("entryText"),
        "stop": x.get("stop"), "tp1": x.get("tp1"), "tp2": x.get("tp2"), "tp3": x.get("tp3"),
        "signalProfile": x.get("signalProfile"), "fingerprint": x.get("fingerprint"),
        "profileThresholds": dict(x.get("profileThresholds") or {}),
    } for x in near[:12]]
    return signals[:max_results]
