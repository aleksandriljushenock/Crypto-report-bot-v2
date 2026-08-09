"""Universe selection and memory-bounded market-data collection."""
from __future__ import annotations
import gc
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from core.runtime_config import boolean, integer, string, scanner_config
from core.runtime_state import update as runtime_update
from main import collect_symbol_data, select_top_symbols
from trade_market_client import create_trade_market_client, collect_multi_exchange_universe
from scanner.analysis import analyze_snapshot

def _set_scan_state(**updates):
    runtime_update("scanner", **updates)

def collect_market_snapshot(extra_symbols=None, top_limit=None):
    client = create_trade_market_client()
    use_multi = boolean('MULTI_EXCHANGE_UNIVERSE_ENABLED', True)
    universe_stats = {}
    if use_multi:
        from config import MIN_QUOTE_VOLUME_USDT
        selected, universe_stats = collect_multi_exchange_universe(
            top_limit=int(top_limit or scanner_config().top_symbols),
            min_quote_volume=scanner_config().min_quote_volume,
            timeout=scanner_config().universe_timeout,
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
        'marketProvider': 'multi-exchange' if use_multi else string('TRADE_MARKET_PROVIDER', 'bybit', strategy=False).lower(),
        'marketProviders': list(getattr(client, 'provider_names', []) or []),
        'universeProviderStats': universe_stats,
        'selectedSymbols': list(selected_map.values()),
        'symbolsData': {},
    }

    max_workers = max(1, min(2, scanner_config().workers))
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
    use_multi = boolean('MULTI_EXCHANGE_UNIVERSE_ENABLED', True)
    provider_stats = {}
    limit = int(top_limit or scanner_config().top_symbols)
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
            min_quote_volume=scanner_config().min_quote_volume,
            timeout=scanner_config().universe_timeout,
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
    batch_size = scanner_config().batch_size
    max_workers = scanner_config().workers
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
        'marketProvider': 'multi-exchange' if use_multi else string('TRADE_MARKET_PROVIDER', 'bybit', strategy=False).lower(),
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
