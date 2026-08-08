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
from trade_market_client import create_trade_market_client, collect_multi_exchange_universe, get_last_universe_summary
from main import collect_symbol_data, select_top_symbols
from rule_engine import evaluate_rules

_SCAN_LOCK = threading.Lock()
from core.runtime_state import finish as runtime_finish, get as runtime_get, start as runtime_start, update as runtime_update


def _set_scan_state(**updates):
    runtime_update('scanner', **updates)


def get_trade_scan_runtime_state():
    return runtime_get('scanner')


def is_trade_scan_running():
    return _SCAN_LOCK.locked()


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



def _select_market_symbols(extra_symbols=None, top_limit=None, only_symbols=None):
    """Return ranked symbols without retaining candle payloads in memory."""
    client = create_trade_market_client()
    use_multi = os.getenv('MULTI_EXCHANGE_UNIVERSE_ENABLED', 'true').strip().lower() in {'1','true','yes','on'}
    provider_stats = {}
    limit = int(top_limit or os.getenv('TRADE_TOP_LIQUID_SYMBOLS', '80'))
    if only_symbols:
        selected = []
        for symbol in list(dict.fromkeys(str(x).upper() for x in only_symbols if x)):
            try:
                ticker = client.ticker_24h(symbol)
                selected.append({
                    'symbol': symbol,
                    'lastPrice': float(ticker.get('lastPrice') or ticker.get('markPrice') or 0),
                    'priceChangePercent': float(ticker.get('priceChangePercent') or 0),
                    'quoteVolume': float(ticker.get('quoteVolume') or 0),
                    'exchanges': [], 'exchangeCount': 0, 'fastScanBucket': 'near_signal',
                })
            except Exception:
                continue
    elif use_multi:
        from config import MIN_QUOTE_VOLUME_USDT
        selected, provider_stats = collect_multi_exchange_universe(
            top_limit=limit,
            min_quote_volume=float(os.getenv('MULTI_EXCHANGE_MIN_QUOTE_VOLUME_USDT', str(MIN_QUOTE_VOLUME_USDT))),
            timeout=int(os.getenv('MULTI_EXCHANGE_UNIVERSE_TIMEOUT', '8')),
        )
    else:
        selected = select_top_symbols(client)[:limit]
    selected_map = {item['symbol']: dict(item) for item in selected}
    for symbol in extra_symbols or []:
        if symbol in selected_map:
            continue
        try:
            ticker = client.ticker_24h(symbol)
            selected_map[symbol] = {
                'symbol': symbol,
                'lastPrice': float(ticker.get('lastPrice') or 0),
                'priceChangePercent': float(ticker.get('priceChangePercent') or 0),
                'quoteVolume': float(ticker.get('quoteVolume') or 0),
                'exchanges': [],
                'exchangeCount': 0,
            }
        except Exception:
            continue
    return client, use_multi, selected_map, provider_stats


def collect_and_analyze_market(extra_symbols=None, top_limit=None, only_symbols=None):
    """Analyze a wider universe in small batches so RAM follows batch size, not universe size."""
    client, use_multi, selected_map, provider_stats = _select_market_symbols(extra_symbols, top_limit, only_symbols=only_symbols)
    run_time = datetime.now(timezone.utc).isoformat()
    items = list(selected_map.items())
    total = len(items)
    batch_size = max(2, min(20, int(os.getenv('TRADE_SCAN_BATCH_SIZE', '8'))))
    max_workers = max(1, min(4, int(os.getenv('TRADE_SCAN_MAX_WORKERS', '2'))))
    _set_scan_state(phase='market_data', processed=0, total=total)

    btc_data = None
    try:
        btc_data = collect_symbol_data(client, 'BTCUSDT')
        btc_data['marketExchanges'] = list((selected_map.get('BTCUSDT') or {}).get('exchanges') or [])
        btc_data['exchangeCount'] = int((selected_map.get('BTCUSDT') or {}).get('exchangeCount') or len(btc_data['marketExchanges']))
    except Exception:
        btc_data = None

    rows = []
    processed = 0
    for start in range(0, total, batch_size):
        batch = items[start:start + batch_size]
        payloads = {}
        with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(batch)))) as executor:
            futures = {executor.submit(collect_symbol_data, client, symbol): (symbol, meta) for symbol, meta in batch}
            for future in as_completed(futures):
                symbol, meta = futures[future]
                try:
                    payload = future.result()
                    payload['marketExchanges'] = list(meta.get('exchanges') or [])
                    payload['exchangeCount'] = int(meta.get('exchangeCount') or len(payload['marketExchanges']))
                    payload['fastScanBucket'] = meta.get('fastScanBucket')
                    payload['crossExchangeChangeMedian'] = meta.get('crossExchangeChangeMedian')
                    payloads[symbol] = payload
                except Exception as exc:
                    payloads[symbol] = {'symbol': symbol, 'error': str(exc)}

        # analyze_snapshot needs BTC context for relative strength. Add it only as context,
        # then discard its duplicate row unless BTC belongs to this batch.
        mini_symbols = dict(payloads)
        batch_symbols = set(payloads)
        if btc_data is not None and 'BTCUSDT' not in mini_symbols:
            mini_symbols['BTCUSDT'] = btc_data
        mini = {'symbolsData': mini_symbols}
        _set_scan_state(phase='analysis', processed=processed, total=total)
        batch_rows = analyze_snapshot(mini)
        for row in batch_rows:
            sym = ((row.get('score') or {}).get('symbol') if isinstance(row, dict) else None) or row.get('symbol')
            if sym in batch_symbols:
                rows.append(row)
        processed += len(batch)
        _set_scan_state(phase='analysis', processed=processed, total=total)
        payloads.clear()
        mini.clear()
        del batch_rows
        gc.collect()

    rows.sort(key=lambda item: item.get('score', {}).get('score', 0), reverse=True)
    return {
        'runTimeUtc': run_time,
        'marketProvider': 'multi-exchange' if use_multi else os.getenv('TRADE_MARKET_PROVIDER', 'bybit').strip().lower(),
        'marketProviders': list(getattr(client, 'provider_names', []) or []),
        'universeProviderStats': provider_stats,
        'selectedSymbols': list(selected_map.values()),
    }, rows


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
            rows.append({'score': score, 'levels': levels, 'rules': rules, 'listing': _listing_metadata(symbol), 'timeframe': timeframe, 'marketExchanges': list(symbol_data.get('marketExchanges') or []), 'exchangeCount': int(symbol_data.get('exchangeCount') or 0), 'fastScanBucket': symbol_data.get('fastScanBucket'), 'crossExchangeChangeMedian': symbol_data.get('crossExchangeChangeMedian'), 'chronosCloses': [c.get('close') for c in symbol_data['parsedKlines'].get(os.getenv('CHRONOS_TIMEFRAME', '15m'), []) if c.get('close') is not None]})
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
        return float(os.getenv(name, str(default)))
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
    return {
        'score': _env_float(prefix + 'SCORE', base_score),
        'rr': _env_float(prefix + 'RR', base_rr),
        'probability': _env_float(prefix + 'PROBABILITY', base_probability),
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

def run_trade_scan(include_watch=False, max_results=5, apply_ai=True, source='unknown', only_symbols=None):
    if not _SCAN_LOCK.acquire(blocking=False):
        return {
            'runTimeUtc': datetime.now(timezone.utc).isoformat(),
            'marketProvider': os.getenv('TRADE_MARKET_PROVIDER', 'unknown'),
            'signals': [], 'rowsAnalyzed': 0, 'busy': True,
            'prioritySymbols': [], 'listingPrioritySymbols': [], 'discoveryPrioritySymbols': [],
            'scanState': get_trade_scan_runtime_state(),
        }
    runtime_start('scanner', owner=source, phase='universe', processed=0, total=0)
    snapshot = None
    rows = None
    try:
        listing_priority = get_priority_listing_symbols(limit=int(os.getenv('TRADE_LISTING_PRIORITY_LIMIT', '12')))
        discovery_priority = get_priority_discovery_symbols(limit=int(os.getenv('TRADE_DISCOVERY_PRIORITY_LIMIT', '10')))
        priority = list(dict.fromkeys(listing_priority + discovery_priority))
        top_limit = int(os.getenv('TRADE_TOP_LIQUID_SYMBOLS', '50'))
        snapshot, rows = collect_and_analyze_market(extra_symbols=None if only_symbols else priority, top_limit=top_limit, only_symbols=only_symbols)
        run_time = snapshot['runTimeUtc']
        provider = snapshot.get('marketProvider', 'unknown')
        universe_summary = get_last_universe_summary()
        universe_summary.setdefault('selectedSymbols', len(snapshot.get('selectedSymbols') or []))
        provider_stats = dict(snapshot.get('universeProviderStats') or {})
        _set_scan_state(phase='ranking', processed=len(rows), total=len(snapshot.get('selectedSymbols') or []))
        snapshot.clear()
        snapshot = None
        gc.collect()
        min_score = float(os.getenv('TRADE_MIN_SCORE', '72'))
        min_rr = float(os.getenv('TRADE_MIN_RR', '2.0'))
        min_probability = float(os.getenv('TRADE_MIN_PROBABILITY', '65'))
        candidate_pool = max(max_results * 2, int(os.getenv('HEDGE_CANDIDATE_POOL', '10'))) if apply_ai else max_results
        filter_diag = {}
        signals = find_trade_signals(
            rows, min_score=min_score, min_rr=min_rr,
            include_watch=include_watch, max_results=candidate_pool, min_probability=min_probability,
            diagnostics=filter_diag,
        )
        ai_diag = {"input": len(signals), "quality": len(signals), "ev": len(signals), "passed": len(signals), "rejected": []}
        if apply_ai:
            _set_scan_state(phase='hedge', processed=0, total=len(signals))
            try:
                from ai_intelligence import rank_signals, get_last_rank_diagnostics
                signals = rank_signals(signals)[:max_results]
                ai_diag = get_last_rank_diagnostics()
            except Exception:
                pass
        _set_scan_state(phase='finalizing', processed=len(signals), total=max(len(signals), 1))
        stages = {
            "analyzed": int(filter_diag.get("analyzed") or len(rows)),
            "status": int(filter_diag.get("status") or 0),
            "score": int(filter_diag.get("score") or 0),
            "rr": int(filter_diag.get("rr") or 0),
            "probability": int(filter_diag.get("probability") or 0),
            "quality": int(ai_diag.get("quality") or 0),
            "ev": int(ai_diag.get("ev") or 0),
            "signals": len(signals),
        }
        market_state = {"LONG_BIAS": 0, "SHORT_BIAS": 0, "NO_TRADE": 0}
        for _row in rows:
            direction = ((_row.get("score") or {}).get("direction") if isinstance(_row, dict) else None) or "NO_TRADE"
            market_state[direction if direction in market_state else "NO_TRADE"] += 1
        distributions = {
            "quality": dict(ai_diag.get("qualityBands") or {}),
            "probability": dict(ai_diag.get("probabilityBands") or {}),
            "ev": dict(ai_diag.get("evBands") or {}),
        }
        near_misses = list(ai_diag.get("rejected") or []) + list(filter_diag.get("nearMisses") or [])
        # v22: near-signal candidates stay hot between full scans, while shadow
        # candidates are tracked without being sent/opened as trades.
        near_watch = []
        try:
            from near_signal_watchlist import select_near_candidates, upsert_near_candidates
            near_watch = select_near_candidates(near_misses)
            upsert_near_candidates(near_watch, source=source)
        except Exception:
            near_watch = []
        try:
            from shadow_signals import register_shadow_candidates
            register_shadow_candidates(near_misses, source=source)
        except Exception:
            pass
        result = {
            'runTimeUtc': run_time, 'marketProvider': provider, 'signals': signals,
            'rowsAnalyzed': len(rows), 'prioritySymbols': priority,
            'listingPrioritySymbols': listing_priority, 'discoveryPrioritySymbols': discovery_priority,
            'universeSummary': universe_summary, 'universeProviderStats': provider_stats,
            'scannerStages': stages, 'nearMisses': near_misses[:8],
            'nearWatch': near_watch[:12], 'nearWatchSymbols': [x.get('symbol') for x in near_watch if x.get('symbol')],
            'scannerDistributions': distributions, 'marketState': market_state,
            'scanSource': source, 'targetedScan': bool(only_symbols),
        }
        try:
            from scanner_intelligence import save_scan_intelligence, build_recommendation
            intelligence = {
                "runTimeUtc": run_time, "marketProvider": provider,
                "universe": universe_summary, "providerStats": provider_stats,
                "stages": stages, "nearMisses": near_misses[:8],
                "distributions": distributions, "marketState": market_state,
                "recommendation": "",
            }
            intelligence["recommendation"] = build_recommendation(intelligence)
            save_scan_intelligence(intelligence)
        except Exception:
            pass
        return result
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
        runtime_finish('scanner')
        _SCAN_LOCK.release()

