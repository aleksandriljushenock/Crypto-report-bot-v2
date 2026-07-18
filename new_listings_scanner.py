import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from alpha_score import calculate_alpha_score
from binance_client import BinanceFuturesClient
from config import BASE_URL, FUTURES_DATA_URL
from research_analyzer import analyze_project_research
from research_collector import collect_project_research
from token_security_analyzer import (
    analyze_token_security,
)
from token_security_collector import (
    collect_token_security,
)
from launch_behavior_analyzer import (
    analyze_launch_behavior,
)

MAX_NEW_SYMBOLS = 100
MAX_WORKERS = 3

MIN_OVERALL_SCORE = 65
MIN_DATA_QUALITY = 50
MIN_MARKET_SCORE = 50
MIN_TOKENOMICS_SCORE = 45

STABLECOINS = {
    "USDC",
    "FDUSD",
    "TUSD",
    "USDE",
    "DAI",
    "USDP",
    "BUSD",
}


def safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_base_asset(symbol):
    if symbol.endswith("USDT"):
        return symbol[:-4]

    return symbol


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


def get_first_kline_time(client, symbol):
    """
    Получает время первой доступной дневной свечи.

    startTime=0 помогает запросить наиболее ранние данные,
    но поведение Binance может различаться, поэтому используется
    как fallback для onboardDate.
    """
    try:
        data = client._get(
            f"{client.base_url}/fapi/v1/klines",
            {
                "symbol": symbol,
                "interval": "1d",
                "startTime": 0,
                "limit": 1,
            },
            use_cache=True,
        )

        if isinstance(data, list) and data:
            return int(data[0][0])

    except Exception:
        pass

    return None


def get_recent_symbols(client, limit=MAX_NEW_SYMBOLS):
    exchange_info = client.exchange_info()
    tickers = client.ticker_24h_all()

    ticker_map = {
        item.get("symbol"): item
        for item in tickers
        if item.get("symbol")
    }

    candidates = []

    for item in exchange_info.get("symbols", []):
        symbol = item.get("symbol")
        base_asset = item.get("baseAsset")

        if not symbol or not base_asset:
            continue

        if item.get("quoteAsset") != "USDT":
            continue

        if item.get("contractType") != "PERPETUAL":
            continue

        if item.get("status") != "TRADING":
            continue

        if base_asset in STABLECOINS:
            continue

        onboard_date = item.get("onboardDate")

        ticker = ticker_map.get(symbol, {})

        candidates.append({
            "symbol": symbol,
            "baseAsset": base_asset,
            "onboardTimestamp": (
                int(onboard_date)
                if onboard_date
                else None
            ),
            "quoteVolume24h": safe_float(
                ticker.get("quoteVolume")
            ),
            "priceChange24h": safe_float(
                ticker.get("priceChangePercent")
            ),
            "lastPrice": safe_float(
                ticker.get("lastPrice")
            ),
        })

    # Сначала используем onboardDate.
    with_onboard = [
        item
        for item in candidates
        if item["onboardTimestamp"]
    ]

    without_onboard = [
        item
        for item in candidates
        if not item["onboardTimestamp"]
    ]

    # Для недостающих дат получаем время первой свечи.
    for item in without_onboard:
        item["onboardTimestamp"] = get_first_kline_time(
            client,
            item["symbol"],
        )

        time.sleep(0.05)

    candidates.sort(
        key=lambda item: item.get("onboardTimestamp") or 0,
        reverse=True,
    )

    return candidates[:limit]


def evaluate_listing_candidate(listing, research):
    if not research.get("available"):
        return {
            "interesting": False,
            "listing": listing,
            "research": research,
            "reasons": [
                "фундаментальные данные недоступны",
            ],
        }

    scores = research.get("scores", {})

    overall_score = research.get("overallScore", 0)
    data_quality = research.get("dataQuality", 0)

    tokenomics_score = scores.get("tokenomics", 0)
    market_score = scores.get("market", 0)
    development_score = scores.get("development", 0)
    adoption_score = scores.get("adoption", 0)

    reasons_against = list(
        research.get("redFlags", [])
    )

    reasons_for = list(
        research.get("positives", [])
    )

    quote_volume = listing.get(
        "quoteVolume24h",
        0,
    )

    price_change = listing.get(
        "priceChange24h",
        0,
    )

    hard_reject = []

    if data_quality < MIN_DATA_QUALITY:
        hard_reject.append(
            "низкое качество исходных данных"
        )

    if overall_score < MIN_OVERALL_SCORE:
        hard_reject.append(
            f"общий рейтинг ниже {MIN_OVERALL_SCORE}"
        )

    if tokenomics_score < MIN_TOKENOMICS_SCORE:
        hard_reject.append(
            "слабая токеномика"
        )

    if market_score < MIN_MARKET_SCORE:
        hard_reject.append(
            "слабые рыночные показатели"
        )

    if quote_volume < 10_000_000:
        hard_reject.append(
            "объем Binance ниже 10M USDT"
        )

    if price_change > 60:
        hard_reject.append(
            "монета уже выросла более чем на 60% за сутки"
        )

    if len(reasons_against) >= 5:
        hard_reject.append(
            "слишком много фундаментальных рисков"
        )

    opportunity_score = round(
        overall_score * 0.45
        + tokenomics_score * 0.15
        + development_score * 0.15
        + adoption_score * 0.10
        + market_score * 0.15
    )

    interesting = (
        not hard_reject
        and opportunity_score >= 65
    )

    return {
        "interesting": interesting,
        "opportunityScore": opportunity_score,
        "listing": listing,
        "research": research,
        "reasonsFor": reasons_for[:5],
        "reasonsAgainst": (
            hard_reject + reasons_against
        )[:7],
    }


def analyze_one_listing(listing):
    base_asset = listing["baseAsset"]

    try:
        raw_research = collect_project_research(
            base_asset
        )

        research = analyze_project_research(
            raw_research
        )

        security_analysis = {
            "available": False,
            "score": None,
            "riskLevel": "UNKNOWN",
            "hardReject": False,
            "positives": [],
            "risks": [],
            "warnings": [],
        }

        coingecko = raw_research.get(
            "coingecko",
            {},
        )

        if coingecko:
            security_raw = (
                collect_token_security(
                    coingecko
                )
            )

            security_analysis = (
                analyze_token_security(
                    security_raw
                )
            )

        client = BinanceFuturesClient(
            base_url=BASE_URL,
            futures_data_url=FUTURES_DATA_URL,
        )

        symbol = listing["symbol"]

        klines_5m = client.klines(
            symbol,
            "5m",
            300,
        )

        klines_1h = client.klines(
            symbol,
            "1h",
            200,
        )

        oi_history = client.open_interest_history(
            symbol,
            period="1h",
            limit=24,
        )

        premium = client.premium_index(
            symbol
        )

        current_funding = premium.get(
            "lastFundingRate"
        )

        launch_behavior = analyze_launch_behavior(
            klines_5m=klines_5m,
            klines_1h=klines_1h,
            oi_history=oi_history,
            current_funding=current_funding,
        )

        base_result = (
            evaluate_listing_candidate(
                listing,
                research,
            )
        )

        alpha = calculate_alpha_score(
            listing=listing,
            research=research,
            security=security_analysis,
        )

        base_result["security"] = (
            security_analysis
        )

        base_result["launchBehavior"] = (
            launch_behavior
        )

        base_result["alpha"] = alpha

        base_result["alphaScore"] = (
            alpha.get("alphaScore", 0)
        )

        base_result["opportunityScore"] = (
            alpha.get("alphaScore", 0)
        )

        launch_score = launch_behavior.get(
            "score",
            0,
        )

        launch_action = launch_behavior.get(
            "action",
            "INSUFFICIENT_DATA",
        )

        launch_passed = (
            launch_behavior.get("available")
            and launch_score >= 60
            and launch_action in [
                "READY_FOR_TECHNICAL_REVIEW",
                "WAIT_FOR_SETUP",
            ]
            and not launch_behavior.get(
                "hardReject",
                False,
            )
        )

        alpha_passed = (
            alpha.get("action")
            in [
                "HIGH_PRIORITY_WATCH",
                "WATCH_FOR_ENTRY",
            ]
            and not alpha.get("hardReject")
        )

        base_result["interesting"] = (
            alpha_passed
            and launch_passed
        )

        if not launch_passed:
            base_result.setdefault(
                "reasonsAgainst",
                [],
            ).insert(
                0,
                (
                    "Launch Behavior не прошёл фильтр: "
                    f"{launch_behavior.get('actionLabel')}, "
                    f"score={launch_score}"
                ),
            )

        if alpha.get("reasonsFor"):
            base_result["reasonsFor"] = (
                alpha["reasonsFor"]
            )

        if alpha.get("reasonsAgainst"):
            base_result[
                "reasonsAgainst"
            ] = alpha["reasonsAgainst"]

        return base_result

    except Exception as exc:
        return {
            "interesting": False,
            "listing": listing,
            "research": {
                "available": False,
                "error": str(exc),
            },
            "alpha": {
                "available": False,
                "alphaScore": 0,
                "grade": "F",
                "action": (
                    "INSUFFICIENT_DATA"
                ),
                "actionLabel": (
                    "⚪ НЕДОСТАТОЧНО ДАННЫХ"
                ),
            },
            "reasonsAgainst": [
                f"ошибка анализа: "
                f"{str(exc)[:150]}"
            ],
        }

def scan_new_listings(limit=MAX_NEW_SYMBOLS):
    client = BinanceFuturesClient(
        base_url=BASE_URL,
        futures_data_url=FUTURES_DATA_URL,
    )

    listings = get_recent_symbols(
        client,
        limit=limit,
    )

    results = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:
        futures = {
            executor.submit(
                analyze_one_listing,
                listing,
            ): listing
            for listing in listings
        }

        for future in as_completed(futures):
            try:
                results.append(
                    future.result()
                )
            except Exception as exc:
                listing = futures[future]

                results.append({
                    "interesting": False,
                    "listing": listing,
                    "reasonsAgainst": [
                        str(exc),
                    ],
                })

    results.sort(
        key=lambda item: item.get(
            "opportunityScore",
            0,
        ),
        reverse=True,
    )

    interesting = [
        item
        for item in results
        if item.get("interesting")
    ]

    return {
        "scannedAtUtc": datetime.now(
            timezone.utc
        ).isoformat(),
        "scannedCount": len(results),
        "interestingCount": len(interesting),
        "interesting": interesting,
        "allResults": results,
    }