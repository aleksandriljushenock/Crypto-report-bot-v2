import time
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)
from datetime import datetime, timezone

from binance_client import BinanceFuturesClient
from config import BASE_URL, FUTURES_DATA_URL
from listing_database import (
    get_database_stats,
    get_interesting_listings,
    get_pending_listings,
    initialize_database,
    mark_research_started,
    reset_stuck_processing,
    save_research_error,
    save_research_result,
    upsert_listing,
)
from new_listings_scanner import analyze_one_listing


DEEP_ANALYSIS_BATCH_SIZE = 100
MAX_WORKERS = 2
REQUEST_PAUSE_SECONDS = 0.5


STABLECOINS = {
    "USDC",
    "FDUSD",
    "TUSD",
    "USDE",
    "USDP",
    "DAI",
    "BUSD",
}


def safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def timestamp_to_iso(timestamp_ms):
    if not timestamp_ms:
        return None

    try:
        return datetime.fromtimestamp(
            int(timestamp_ms) / 1000,
            tz=timezone.utc,
        ).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def synchronize_binance_universe():
    client = BinanceFuturesClient(
        base_url=BASE_URL,
        futures_data_url=FUTURES_DATA_URL,
    )

    exchange_info = client.exchange_info()
    tickers = client.ticker_24h_all()

    ticker_map = {
        ticker.get("symbol"): ticker
        for ticker in tickers
        if ticker.get("symbol")
    }

    saved = 0

    for symbol_info in exchange_info.get(
        "symbols",
        [],
    ):
        symbol = symbol_info.get("symbol")
        base_asset = symbol_info.get("baseAsset")
        quote_asset = symbol_info.get("quoteAsset")
        contract_type = symbol_info.get(
            "contractType"
        )
        status = symbol_info.get("status")

        if not symbol or not base_asset:
            continue

        if quote_asset != "USDT":
            continue

        if contract_type != "PERPETUAL":
            continue

        if base_asset in STABLECOINS:
            continue

        ticker = ticker_map.get(
            symbol,
            {},
        )

        onboard_timestamp = symbol_info.get(
            "onboardDate"
        )

        listing = {
            "symbol": symbol,
            "baseAsset": base_asset,
            "quoteAsset": quote_asset,
            "contractType": contract_type,
            "status": status,
            "onboardTimestamp": (
                int(onboard_timestamp)
                if onboard_timestamp
                else None
            ),
            "onboardIso": timestamp_to_iso(
                onboard_timestamp
            ),
            "lastPrice": safe_float(
                ticker.get("lastPrice")
            ),
            "quoteVolume24h": safe_float(
                ticker.get("quoteVolume")
            ),
            "priceChange24h": safe_float(
                ticker.get("priceChangePercent")
            ),
        }

        upsert_listing(listing)
        saved += 1

    return saved


def database_row_to_listing(row):
    return {
        "symbol": row.get("symbol"),
        "baseAsset": row.get("base_asset"),
        "onboardTimestamp": row.get(
            "onboard_timestamp"
        ),
        "quoteVolume24h": row.get(
            "quote_volume_24h"
        ) or 0,
        "priceChange24h": row.get(
            "price_change_24h"
        ) or 0,
        "lastPrice": row.get("last_price") or 0,
    }


def analyze_database_row(row):
    symbol = row["symbol"]

    mark_research_started(symbol)

    try:
        time.sleep(REQUEST_PAUSE_SECONDS)

        listing = database_row_to_listing(
            row
        )

        result = analyze_one_listing(
            listing
        )

        save_research_result(
            symbol,
            result,
        )

        return result

    except Exception as exc:
        save_research_error(
            symbol,
            exc,
        )

        return {
            "interesting": False,
            "listing": {
                "symbol": symbol,
                "baseAsset": row.get(
                    "base_asset"
                ),
            },
            "research": {
                "available": False,
                "error": str(exc),
            },
            "reasonsAgainst": [
                f"ошибка анализа: {str(exc)[:200]}"
            ],
        }


def run_incremental_listing_scan(
    deep_limit=DEEP_ANALYSIS_BATCH_SIZE,
):
    initialize_database()
    reset_stuck_processing()

    synchronized_count = (
        synchronize_binance_universe()
    )

    pending = get_pending_listings(
        limit=deep_limit,
    )

    current_results = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:
        futures = [
            executor.submit(
                analyze_database_row,
                row,
            )
            for row in pending
        ]

        for future in as_completed(futures):
            try:
                current_results.append(
                    future.result()
                )
            except Exception:
                pass

    interesting_database = (
        get_interesting_listings(
            limit=20
        )
    )

    interesting_results = [
        item.get("analysis", {})
        for item in interesting_database
        if item.get("analysis")
    ]

    return {
        "scannedAtUtc": datetime.now(
            timezone.utc
        ).isoformat(),
        "binanceSymbolsSaved": (
            synchronized_count
        ),
        "deepAnalyzedThisRun": len(
            current_results
        ),
        "interestingCount": len(
            interesting_results
        ),
        "interesting": interesting_results,
        "databaseStats": get_database_stats(),
    }