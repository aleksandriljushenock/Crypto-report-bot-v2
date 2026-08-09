import json
from datetime import datetime, timezone
from pathlib import Path

from binance_client import BinanceFuturesClient
from config import (
    BASE_URL,
    FUTURES_DATA_URL,
    MIN_QUOTE_VOLUME_USDT,
    TOP_LIMIT,
    INTERVALS,
    ORDERBOOK_LIMIT,
    LONG_SHORT_PERIOD,
    TAKER_VOLUME_PERIOD,
)


def to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def get_usdt_perpetual_symbols(client):
    info = client.exchange_info()
    symbols = set()

    for item in info.get("symbols", []):
        if (
            item.get("quoteAsset") == "USDT"
            and item.get("contractType") == "PERPETUAL"
            and item.get("status") == "TRADING"
        ):
            symbols.add(item["symbol"])

    return symbols


def select_top_symbols(client):
    tradable_symbols = get_usdt_perpetual_symbols(client)
    tickers = client.ticker_24h_all()

    filtered = []

    for ticker in tickers:
        symbol = ticker.get("symbol")

        if symbol not in tradable_symbols:
            continue

        quote_volume = to_float(ticker.get("quoteVolume"))

        if quote_volume < MIN_QUOTE_VOLUME_USDT:
            continue

        filtered.append(
            {
                "symbol": symbol,
                "lastPrice": to_float(ticker.get("lastPrice")),
                "priceChangePercent": to_float(ticker.get("priceChangePercent")),
                "quoteVolume": quote_volume,
                "highPrice": to_float(ticker.get("highPrice")),
                "lowPrice": to_float(ticker.get("lowPrice")),
                "count": ticker.get("count"),
            }
        )

    filtered.sort(key=lambda x: x["quoteVolume"], reverse=True)
    return filtered[:TOP_LIMIT]


def safe_call(name, func):
    try:
        return func()
    except Exception as exc:
        return {
            "error": str(exc)
        }


def _market_call(client, method, *args, **kwargs):
    """Call a normalized market capability without inventing numeric zero.

    FallbackTradeMarketClient exposes explicit capability status. Legacy clients
    still use safe_call, keeping app.py/backfills compatible.
    """
    capability = getattr(client, "capability", None)
    if callable(capability):
        result = capability(method, *args, **kwargs)
        if result.available:
            return result.value
        return {"error": result.error or result.status, "capability": result.status}
    return safe_call(method, lambda: getattr(client, method)(*args, **kwargs))


def collect_symbol_data(client, symbol):
    data = {
        "symbol": symbol,
        "ticker24h": _market_call(client, "ticker_24h", symbol),
        "openInterest": _market_call(client, "open_interest", symbol),
        "openInterestHistory": _market_call(client, "open_interest_history", symbol, period="1h", limit=24),
        "premiumIndex": _market_call(client, "premium_index", symbol),
        "depth": _market_call(client, "depth", symbol, ORDERBOOK_LIMIT),
        "longShortRatio": _market_call(client, "global_long_short_ratio", symbol=symbol, period=LONG_SHORT_PERIOD, limit=24),
        "takerBuySellVolume": _market_call(client, "taker_buy_sell_volume", symbol=symbol, period=TAKER_VOLUME_PERIOD, limit=24),
        "klines": {},
    }
    for interval, limit in INTERVALS.items():
        data["klines"][interval] = _market_call(client, "klines", symbol, interval, limit)
    return data


def main():
    client = BinanceFuturesClient(
        base_url=BASE_URL,
        futures_data_url=FUTURES_DATA_URL,
    )

    run_time = datetime.now(timezone.utc).isoformat()
    output_dir = Path("data")
    output_dir.mkdir(exist_ok=True)

    print("Selecting top symbols...")
    top_symbols = select_top_symbols(client)

    print(f"Selected {len(top_symbols)} symbols")

    result = {
        "runTimeUtc": run_time,
        "filters": {
            "market": "Binance USD-M Futures",
            "quoteAsset": "USDT",
            "contractType": "PERPETUAL",
            "minQuoteVolumeUSDT": MIN_QUOTE_VOLUME_USDT,
            "topLimit": TOP_LIMIT,
        },
        "selectedSymbols": top_symbols,
        "symbolsData": {},
    }

    for index, item in enumerate(top_symbols, start=1):
        symbol = item["symbol"]
        progress = round(index / len(top_symbols) * 100, 1)
        print(f"[{index}/{len(top_symbols)} | {progress}%] Collecting {symbol}...", flush=True)

        try:
            result["symbolsData"][symbol] = collect_symbol_data(client, symbol)
        except Exception as exc:
            result["symbolsData"][symbol] = {
                "symbol": symbol,
                "error": str(exc),
            }
            print(f"ERROR {symbol}: {exc}", flush=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"binance_snapshot_{timestamp}.json"

    with output_path.open("w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2)

    print(f"Saved: {output_path}")


if __name__ == "__main__":
    main()