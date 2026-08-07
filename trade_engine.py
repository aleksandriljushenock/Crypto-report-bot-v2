import gc
import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from analyzer import (
    analyze_fvg_multiple_timeframes,
    analyze_multiple_timeframes,
    analyze_order_blocks_multiple_timeframes,
    build_trade_levels,
    calculate_funding_analysis,
    calculate_oi_analysis,
    calculate_relative_strength,
    calculate_score,
    parse_klines,
)
from trade_market_client import create_trade_market_client, collect_multi_exchange_universe
from main import collect_symbol_data, select_top_symbols
from rule_engine import evaluate_rules

_SCAN_LOCK = threading.Lock()


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


def collect_market_snapshot(extra_symbols=None, top_limit=None):
    client = create_trade_market_client()
    use_multi = os.getenv('MULTI_EXCHANGE_UNIVERSE_ENABLED', 'true').strip().lower() in {'1','true','yes','on'}
    universe_stats = {}
    if use_multi:
        from config import MIN_QUOTE_VOLUME_USDT
        selected, universe_stats = collect_multi_exchange_universe(
            top_limit=int(top_limit or os.getenv('TRADE_TOP_LIQUID_SYMBOLS', '30')),
            min_quote_volume=float(os.getenv('MULTI_EXCHANGE_MIN_QUOTE_VOLUME_USDT', str(MIN_QUOTE_VOLUME_USDT))),
            timeout=int(os.getenv('MULTI_EXCHANGE_UNIVERSE_TIMEOUT', '8')),
        )
    else:
        selected = select_top_symbols(client)
        if top_limit:
            selected = selected[:int(top_limit)]
    selected_map = {item['symbol']: item for item in selected}

    tradable = set(selected_map)
    if not use_multi:
        try:
            for item in client.exchange_info().get('symbols', []):
                if item.get('status') == 'TRADING' and item.get('quoteAsset') == 'USDT' and item.get('contractType') == 'PERPETUAL':
                    tradable.add(item['symbol'])
        except Exception:
            tradable = set(selected_map)

    for symbol in extra_symbols or []:
        if symbol not in selected_map and (use_multi or symbol in tradable):
            try:
                ticker = client.ticker_24h(symbol)
                selected_map[symbol] = {
                    'symbol': symbol,
                    'lastPrice': float(ticker.get('lastPrice') or 0),
                    'priceChangePercent': float(ticker.get('priceChangePercent') or 0),
                    'quoteVolume': float(ticker.get('quoteVolume') or 0),
                }
            except Exception:
                continue

    result = {
        'runTimeUtc': datetime.now(timezone.utc).isoformat(),
        'marketProvider': 'multi-exchange' if use_multi else os.getenv('TRADE_MARKET_PROVIDER', 'bybit').strip().lower(),
        'marketProviders': list(getattr(client, 'provider_names', []) or []),
        'universeProviderStats': universe_stats,
        'selectedSymbols': list(selected_map.values()),
        'symbolsData': {},
    }

    max_workers = max(1, min(2, int(os.getenv('TRADE_SCAN_MAX_WORKERS', '1'))))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(collect_symbol_data, client, symbol): symbol for symbol in selected_map}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                payload = future.result()
                meta = selected_map.get(symbol) or {}
                payload['marketExchanges'] = list(meta.get('exchanges') or [])
                payload['exchangeCount'] = int(meta.get('exchangeCount') or len(payload['marketExchanges']))
                result['symbolsData'][symbol] = payload
            except Exception as exc:
                result['symbolsData'][symbol] = {'symbol': symbol, 'error': str(exc)}
    return result


def get_priority_listing_symbols(limit=20):
    symbols = []
    try:
        from listing_database import get_connection
        with get_connection() as conn:
            rows = conn.execute('''
                SELECT symbol FROM listings
                WHERE exchange_status = 'TRADING'
                  AND (interesting = 1 OR opportunity_score >= 60)
                ORDER BY COALESCE(opportunity_score, 0) DESC,
                         COALESCE(onboard_timestamp, 0) DESC
                LIMIT ?
            ''', (limit,)).fetchall()
        symbols.extend(row['symbol'] for row in rows)
    except Exception:
        pass
    return list(dict.fromkeys(symbols))




def get_priority_discovery_symbols(limit=20):
    symbols = []
    try:
        from early_discovery_database import get_connection
        with get_connection() as conn:
            rows = conn.execute("""
                SELECT symbol FROM discovered_projects
                WHERE status = 'DONE'
                  AND interesting = 1
                  AND symbol IS NOT NULL
                ORDER BY COALESCE(prelisting_score, 0) DESC, discovered_at DESC
                LIMIT ?
            """, (limit,)).fetchall()
        symbols.extend(str(row['symbol']).upper() + 'USDT' for row in rows)
    except Exception:
        pass
    return list(dict.fromkeys(symbols))

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
            levels = build_trade_levels(symbol_data)
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
            rows.append({'score': score, 'levels': levels, 'rules': rules, 'listing': _listing_metadata(symbol), 'timeframe': timeframe, 'marketExchanges': list(symbol_data.get('marketExchanges') or []), 'exchangeCount': int(symbol_data.get('exchangeCount') or 0), 'chronosCloses': [c.get('close') for c in symbol_data['parsedKlines'].get(os.getenv('CHRONOS_TIMEFRAME', '15m'), []) if c.get('close') is not None]})
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
    oi = 70
    oi_analysis = score.get('oiAnalysis') or {}
    if oi_analysis.get('signal') in ('BULLISH', 'BEARISH'):
        oi = 85
    elif oi_analysis.get('signal') == 'WEAK':
        oi = 45
    risk = max(0, min(100, 45 + min(float(rr or 0), 4) * 12 + (10 if rules.get('finalStatus') == 'TRADE_CANDIDATE' else 0)))
    probability = round(max(5, min(95, 0.30 * momentum + 0.25 * trend + 0.15 * volume + 0.10 * funding + 0.10 * oi + 0.10 * risk)))
    confidence = round(max(5, min(95, 0.45 * trend + 0.30 * momentum + 0.25 * risk)))
    return {
        'trend': round(trend), 'momentum': round(momentum), 'volume': round(volume),
        'funding': round(funding), 'oi': round(oi), 'risk': round(risk),
        'probability': probability, 'confidence': confidence,
    }

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
        # Kept only until final AI ranking; removed before persistence.
        '_chronosCloses': list(row.get('chronosCloses') or []),
    }


def find_trade_signals(rows, min_score=72, min_rr=2.0, include_watch=False, max_results=5, min_probability=65):
    signals = []
    for row in rows:
        if 'error' in row or not row.get('rules'):
            continue
        status = row['rules'].get('finalStatus')
        if status != 'TRADE_CANDIDATE' and not (include_watch and status == 'WATCH'):
            continue
        signal = row_to_signal(row)
        threshold = min_score if status == 'TRADE_CANDIDATE' else min_score + 3
        if float(signal.get('score') or 0) < threshold:
            continue
        if float(signal.get('rr') or 0) < min_rr:
            continue
        # Chronos runs later, only for the best final candidates after Hedge pre-ranking.
        # This avoids loading PyTorch while dozens of rows and candle payloads are still alive.
        if float(signal.get('probability') or 0) < min_probability:
            continue
        signals.append(signal)
    signals.sort(key=lambda s: (s['status'] == 'TRADE_CANDIDATE', s['score'], s['rr']), reverse=True)
    return signals[:max_results]


def run_trade_scan(include_watch=False, max_results=5, apply_ai=True):
    if not _SCAN_LOCK.acquire(blocking=False):
        return {
            'runTimeUtc': datetime.now(timezone.utc).isoformat(),
            'marketProvider': os.getenv('TRADE_MARKET_PROVIDER', 'unknown'),
            'signals': [], 'rowsAnalyzed': 0, 'busy': True,
            'prioritySymbols': [], 'listingPrioritySymbols': [], 'discoveryPrioritySymbols': [],
        }
    snapshot = None
    rows = None
    try:
        listing_priority = get_priority_listing_symbols(limit=int(os.getenv('TRADE_LISTING_PRIORITY_LIMIT', '12')))
        discovery_priority = get_priority_discovery_symbols(limit=int(os.getenv('TRADE_DISCOVERY_PRIORITY_LIMIT', '10')))
        priority = list(dict.fromkeys(listing_priority + discovery_priority))
        top_limit = int(os.getenv('TRADE_TOP_LIQUID_SYMBOLS', '16'))
        snapshot = collect_market_snapshot(extra_symbols=priority, top_limit=top_limit)
        rows = analyze_snapshot(snapshot)
        run_time = snapshot['runTimeUtc']
        provider = snapshot.get('marketProvider', 'unknown')
        # Release the largest raw candle structures before ranking/AI.
        snapshot.clear()
        snapshot = None
        gc.collect()
        min_score = float(os.getenv('TRADE_MIN_SCORE', '72'))
        min_rr = float(os.getenv('TRADE_MIN_RR', '2.0'))
        min_probability = float(os.getenv('TRADE_MIN_PROBABILITY', '65'))
        candidate_pool = max(max_results * 2, int(os.getenv('HEDGE_CANDIDATE_POOL', '10'))) if apply_ai else max_results
        signals = find_trade_signals(
            rows, min_score=min_score, min_rr=min_rr,
            include_watch=include_watch, max_results=candidate_pool, min_probability=min_probability,
        )
        if apply_ai:
            try:
                from ai_intelligence import rank_signals
                signals = rank_signals(signals)[:max_results]
            except Exception:
                pass
        return {
            'runTimeUtc': run_time, 'marketProvider': provider, 'signals': signals,
            'rowsAnalyzed': len(rows), 'prioritySymbols': priority,
            'listingPrioritySymbols': listing_priority, 'discoveryPrioritySymbols': discovery_priority,
        }
    finally:
        if snapshot is not None:
            snapshot.clear()
        if rows is not None:
            rows.clear()
        try:
            from memory_guard import cleanup
            cleanup()
        except Exception:
            gc.collect()
        _SCAN_LOCK.release()

