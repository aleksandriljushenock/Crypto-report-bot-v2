import math
from concurrent.futures import ThreadPoolExecutor, as_completed

from trade_market_client import create_trade_market_client
from v8_store import save_snapshot


def _clamp(x):
    return max(0.0, min(100.0, float(x)))


def analyze_symbol(symbol):
    client = create_trade_market_client()
    ticker = client.ticker_24h(symbol)
    premium = client.premium_index(symbol)
    oi = client.open_interest(symbol)
    klines = client.klines(symbol, '15m', 48)
    taker = client.taker_buy_sell_volume(symbol, '15m', 12)
    closes = [float(k[4]) for k in klines]
    vols = [float(k[5]) for k in klines]
    ret = (closes[-1] / closes[-9] - 1) * 100 if len(closes) >= 9 and closes[-9] else 0
    vol_ratio = (sum(vols[-4:]) / 4) / (sum(vols[-20:-4]) / 16 or 1) if len(vols) >= 20 else 1
    last_taker = taker[-1] if taker else {}
    buy_sell = float(last_taker.get('buySellRatio') or last_taker.get('longShortRatio') or 1)
    funding = float(premium.get('lastFundingRate') or 0) * 100
    oi_usd = float(oi.get('openInterest') or 0) * float(ticker.get('lastPrice') or 0)
    long_score = _clamp(50 + ret * 4 + (vol_ratio - 1) * 15 + (buy_sell - 1) * 25 - max(0, funding) * 700)
    short_score = _clamp(50 - ret * 4 + (vol_ratio - 1) * 15 + (1 - buy_sell) * 25 + min(0, funding) * -700)
    direction = 'LONG' if long_score >= short_score else 'SHORT'
    score = max(long_score, short_score)
    result = {
        'symbol': symbol, 'direction': direction, 'score': round(score, 1),
        'priceChange15m8': round(ret, 3), 'volumeRatio': round(vol_ratio, 2),
        'takerBuySellRatio': round(buy_sell, 3), 'fundingPct': round(funding, 5),
        'openInterestUsd': round(oi_usd, 2),
        'liquidationHeatmap': 'proxy: volatility+OI; external provider optional',
        'cvdProxy': round((buy_sell - 1) * 100, 2),
        'provider': getattr(client, 'last_provider', None),
    }
    save_snapshot('capital_flow', result, symbol, score)
    return result


def scan_capital_flows(limit=20):
    client = create_trade_market_client()
    rows = client.ticker_24h_all()
    symbols = [r['symbol'] for r in sorted(rows, key=lambda x: float(x.get('quoteVolume') or 0), reverse=True) if r['symbol'].endswith('USDT')][:max(5, int(limit))]
    out = []
    with ThreadPoolExecutor(max_workers=min(6, len(symbols))) as ex:
        fut = {ex.submit(analyze_symbol, s): s for s in symbols}
        for f in as_completed(fut):
            try:
                out.append(f.result())
            except Exception:
                continue
    return sorted(out, key=lambda x: x['score'], reverse=True)


def build_capital_flow_report(limit=10):
    items = scan_capital_flows(max(limit, 20))[:limit]
    lines = ['<b>💸 CAPITAL FLOW ENGINE</b>', '']
    for i, x in enumerate(items, 1):
        lines += [
            f"<b>{i}. {x['symbol']} — {x['direction']} {x['score']}</b>",
            f"OI ${x['openInterestUsd']:,.0f} | Funding {x['fundingPct']:.4f}%",
            f"Taker {x['takerBuySellRatio']:.2f} | Vol x{x['volumeRatio']:.2f} | CVD proxy {x['cvdProxy']:+.1f}",
            f"Источник: {x.get('provider') or 'fallback'}", ''
        ]
    return '\n'.join(lines)
