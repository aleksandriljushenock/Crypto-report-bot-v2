import time
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)
from datetime import datetime, timezone

from binance_client import BinanceFuturesClient
from config import BASE_URL, FUTURES_DATA_URL
from listing_cache import (
    get_cache_stats,
    initialize_cache,
)
from listing_cached_analyzer import (
    analyze_listing_cached,
)
from listing_database import (
    get_connection,
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


DEEP_ANALYSIS_BATCH_SIZE = 100
REFRESH_EXISTING_LIMIT = 30
MAX_WORKERS = 2
REQUEST_PAUSE_SECONDS = 0.4


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
    except (
        TypeError,
        ValueError,
        OSError,
    ):
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
        base_asset = symbol_info.get(
            "baseAsset"
        )
        quote_asset = symbol_info.get(
            "quoteAsset"
        )
        contract_type = symbol_info.get(
            "contractType"
        )
        status = symbol_info.get(
            "status"
        )

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

        onboard_timestamp = (
            symbol_info.get("onboardDate")
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
                ticker.get(
                    "priceChangePercent"
                )
            ),
        }

        upsert_listing(listing)
        saved += 1

    return saved


def database_row_to_listing(row):
    return {
        "symbol": row.get("symbol"),
        "baseAsset": row.get(
            "base_asset"
        ),
        "onboardTimestamp": row.get(
            "onboard_timestamp"
        ),
        "quoteVolume24h": row.get(
            "quote_volume_24h"
        ) or 0,
        "priceChange24h": row.get(
            "price_change_24h"
        ) or 0,
        "lastPrice": row.get(
            "last_price"
        ) or 0,
    }


def get_refresh_candidates(
    limit=REFRESH_EXISTING_LIMIT,
):
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM listings
            WHERE exchange_status = 'TRADING'
              AND research_status = 'DONE'
            ORDER BY
                interesting DESC,
                COALESCE(
                    opportunity_score,
                    0
                ) DESC,
                COALESCE(
                    onboard_timestamp,
                    0
                ) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


def combine_candidates(
    pending,
    refresh,
):
    result = []
    seen = set()

    for row in pending + refresh:
        symbol = row.get("symbol")

        if not symbol:
            continue

        if symbol in seen:
            continue

        seen.add(symbol)
        result.append(row)

    return result


def analyze_database_row(row):
    symbol = row["symbol"]

    mark_research_started(symbol)

    try:
        time.sleep(
            REQUEST_PAUSE_SECONDS
        )

        listing = database_row_to_listing(
            row
        )

        result = analyze_listing_cached(
            listing,
            force_research_refresh=False,
            force_security_refresh=False,
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
            "buyReadiness": {
                "score": 0,
                "action": "SKIP",
                "actionLabel": (
                    "🔴 ПРОПУСТИТЬ"
                ),
            },
            "reasonsAgainst": [
                (
                    "Ошибка анализа: "
                    f"{str(exc)[:300]}"
                )
            ],
        }


def run_incremental_listing_scan(
    deep_limit=DEEP_ANALYSIS_BATCH_SIZE,
):
    initialize_database()
    initialize_cache()
    reset_stuck_processing()

    synchronized_count = (
        synchronize_binance_universe()
    )

    pending = get_pending_listings(
        limit=deep_limit,
    )

    refresh_candidates = (
        get_refresh_candidates(
            limit=REFRESH_EXISTING_LIMIT,
        )
    )

    candidates = combine_candidates(
        pending,
        refresh_candidates,
    )

    current_results = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:
        future_map = {
            executor.submit(
                analyze_database_row,
                row,
            ): row
            for row in candidates
        }

        for future in as_completed(
            future_map
        ):
            row = future_map[future]

            try:
                result = future.result()
                current_results.append(
                    result
                )

            except Exception as exc:
                symbol = row.get(
                    "symbol",
                    "UNKNOWN",
                )

                save_research_error(
                    symbol,
                    exc,
                )

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

    interesting_results.sort(
        key=lambda item: (
            item.get(
                "buyReadiness",
                {},
            ).get(
                "score",
                item.get(
                    "opportunityScore",
                    0,
                ),
            )
        ),
        reverse=True,
    )

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

        "newProjectsAnalyzed": len(
            pending
        ),

        "existingProjectsRefreshed": len(
            refresh_candidates
        ),

        "interestingCount": len(
            interesting_results
        ),

        "interesting": (
            interesting_results
        ),

        "databaseStats": (
            get_database_stats()
        ),

        "cacheStats": (
            get_cache_stats()
        ),
    }