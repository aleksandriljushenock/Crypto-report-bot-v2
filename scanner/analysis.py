"""Deep market analysis: indicators, structure and normalized trade profiles."""
from __future__ import annotations
from datetime import datetime, timezone
from analyzer import (
    analyze_fvg_multiple_timeframes, analyze_multiple_timeframes,
    analyze_order_blocks_multiple_timeframes, build_trade_levels,
    calculate_funding_analysis, calculate_oi_analysis, calculate_relative_strength,
    calculate_score, parse_klines,
)
from rule_engine import evaluate_rules
from core.runtime_config import string

def _listing_metadata(symbol):
    metadata = {'isRecentListing': False, 'listingScore': None, 'listingAgeDays': None}
    try:
        from listing_database import get_connection
        with get_connection() as conn:
            row = conn.execute('''
                SELECT onboard_timestamp, opportunity_score, interesting
                FROM listings WHERE symbol = ?
            ''', (symbol,)).fetchone()
        if row:
            onboard = row['onboard_timestamp']
            if onboard:
                age_days = max(0, (datetime.now(timezone.utc).timestamp() * 1000 - onboard) / 86400000)
                metadata['listingAgeDays'] = round(age_days, 1)
                metadata['isRecentListing'] = age_days <= 90
            metadata['listingScore'] = row['opportunity_score']
            metadata['listingInteresting'] = bool(row['interesting'])
    except Exception:
        pass
    return metadata


def analyze_snapshot(snapshot):
    rows = []
    symbols_data = snapshot.get('symbolsData', {})
    btc_data = symbols_data.get('BTCUSDT')
    if btc_data and 'error' not in btc_data:
        btc_data['parsedKlines'] = {
            '15m': parse_klines(btc_data.get('klines', {}).get('15m', [])),
            '1h': parse_klines(btc_data.get('klines', {}).get('1h', [])),
            '4h': parse_klines(btc_data.get('klines', {}).get('4h', [])),
        }

    for symbol, symbol_data in symbols_data.items():
        if 'error' in symbol_data:
            continue
        try:
            score = calculate_score(symbol_data)
            levels = build_trade_levels(symbol_data, score.get('direction'))
            symbol_data['parsedKlines'] = {
                '15m': parse_klines(symbol_data.get('klines', {}).get('15m', [])),
                '1h': parse_klines(symbol_data.get('klines', {}).get('1h', [])),
                '4h': parse_klines(symbol_data.get('klines', {}).get('4h', [])),
            }
            score['smcAnalysis'] = analyze_multiple_timeframes(symbol_data['parsedKlines'])
            score['orderBlockAnalysis'] = analyze_order_blocks_multiple_timeframes(symbol_data['parsedKlines'])
            score['fvgAnalysis'] = analyze_fvg_multiple_timeframes(symbol_data['parsedKlines'])
            score['relativeStrength'] = calculate_relative_strength(symbol_data, btc_data)
            score['oiAnalysis'] = calculate_oi_analysis(symbol_data)
            score['fundingAnalysis'] = calculate_funding_analysis(symbol_data)
            rules = evaluate_rules(score, levels)
            timeframe = build_timeframe_profile(symbol_data, score.get('direction'))
            rows.append({'score': score, 'levels': levels, 'rules': rules, 'listing': _listing_metadata(symbol), 'timeframe': timeframe, 'marketExchanges': list(symbol_data.get('marketExchanges') or []), 'exchangeCount': int(symbol_data.get('exchangeCount') or 0), 'fastScanBucket': symbol_data.get('fastScanBucket'), 'crossExchangeChangeMedian': symbol_data.get('crossExchangeChangeMedian'), 'chronosCloses': [c.get('close') for c in symbol_data['parsedKlines'].get(string('CHRONOS_TIMEFRAME', '15m'), []) if c.get('close') is not None]})
        except Exception as exc:
            rows.append({'symbol': symbol, 'error': str(exc)})
    rows.sort(key=lambda item: item.get('score', {}).get('score', 0), reverse=True)
    return rows


def _direction_for_candles(candles):
    if len(candles) < 30:
        return 'N/A', 0
    closes = [c['close'] for c in candles]
    from indicators import ema
    fast = ema(closes, 20)
    slow = ema(closes, 50)
    last = closes[-1]
    if fast is None or slow is None:
        return 'N/A', 0
    if last > fast > slow:
        return 'UP', 1
    if last < fast < slow:
        return 'DOWN', -1
    return 'RANGE', 0


def build_timeframe_profile(symbol_data, direction):
    labels = {}
    numeric = {}
    for interval in ('1d', '4h', '1h', '15m', '5m'):
        candles = parse_klines(symbol_data.get('klines', {}).get(interval, []))
        label, value = _direction_for_candles(candles)
        labels[interval] = label
        numeric[interval] = value
    target = 1 if direction == 'LONG_BIAS' else (-1 if direction == 'SHORT_BIAS' else 0)
    weights = {'1d': 25, '4h': 25, '1h': 25, '15m': 15, '5m': 10}
    aligned = 0
    if target:
        for interval, weight in weights.items():
            if numeric.get(interval) == target:
                aligned += weight
            elif numeric.get(interval) == 0:
                aligned += weight * 0.35
    return {
        'labels': labels,
        'alignment': round(aligned, 1),
    }


def build_trade_profile(score, rules, rr, timeframe):
    trend = min(100, max(0, timeframe.get('alignment', 0)))
    momentum = min(100, max(0, float(score.get('score') or 0)))
    volume = min(100, 45 + min(35, float(score.get('relativeVolume15m') or 0) * 20) + min(20, float(score.get('relativeVolume1h') or 0) * 10))
    funding = 90 if abs(float(score.get('fundingPercent') or 0)) <= 0.02 else max(20, 80 - abs(float(score.get('fundingPercent') or 0)) * 500)
    oi_analysis = score.get('oiAnalysis') or {}
    oi_label = str(oi_analysis.get('label') or 'NO_DATA')
    oi_change_4h = oi_analysis.get('oiChange4h')
    # Neutral 50 means "no evidence". Growth is informative only as activity;
    # direction is learned jointly with price/funding instead of hard-coding bullishness.
    oi = 50
    if oi_change_4h is not None:
        change = max(-10.0, min(10.0, float(oi_change_4h)))
        oi = round(max(20, min(80, 50 + abs(change) * 3)))
    if oi_label == 'OI_DROPPING_FAST':
        oi = min(oi, 40)
    risk = max(0, min(100, 45 + min(float(rr or 0), 4) * 12 + (10 if rules.get('finalStatus') == 'TRADE_CANDIDATE' else 0)))
    probability = round(max(5, min(95, 0.30 * momentum + 0.25 * trend + 0.15 * volume + 0.10 * funding + 0.10 * oi + 0.10 * risk)))
    confidence = round(max(5, min(95, 0.45 * trend + 0.30 * momentum + 0.25 * risk)))
    return {
        'trend': round(trend), 'momentum': round(momentum), 'volume': round(volume),
        'funding': round(funding), 'oi': round(oi), 'risk': round(risk),
        'probability': probability, 'confidence': confidence,
    }
