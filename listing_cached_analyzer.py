from alpha_score import calculate_alpha_score
from binance_client import BinanceFuturesClient
from config import BASE_URL, FUTURES_DATA_URL
from launch_behavior_analyzer import (
    analyze_launch_behavior,
)
from listing_cache import (
    get_cached_research,
    get_cached_security,
    save_cached_research,
    save_cached_security,
    save_latest_result,
    save_research_error,
    save_security_error,
)
from new_listings_scanner import (
    evaluate_listing_candidate,
)
from research_analyzer import (
    analyze_project_research,
)
from research_collector import (
    collect_project_research,
)
from token_security_analyzer import (
    analyze_token_security,
)
from token_security_collector import (
    collect_token_security,
)


RESEARCH_TTL_HOURS = 24
SECURITY_TTL_HOURS = 24 * 7


def empty_security():
    return {
        "available": False,
        "score": None,
        "riskLevel": "UNKNOWN",
        "hardReject": False,
        "positives": [],
        "risks": [],
        "warnings": [
            "Проверка безопасности недоступна"
        ],
    }


def collect_research_with_cache(
    listing,
    force_refresh=False,
):
    symbol = listing["symbol"]
    base_asset = listing["baseAsset"]

    if not force_refresh:
        cached = get_cached_research(
            symbol,
            ttl_hours=RESEARCH_TTL_HOURS,
        )

        if cached:
            return cached, True

    try:
        raw_research = (
            collect_project_research(
                base_asset
            )
        )

        research = analyze_project_research(
            raw_research
        )

        bundle = {
            "raw": raw_research,
            "analysis": research,
        }

        save_cached_research(
            symbol,
            bundle,
        )

        return bundle, False

    except Exception as exc:
        save_research_error(
            symbol,
            exc,
        )
        raise


def collect_security_with_cache(
    listing,
    raw_research,
    force_refresh=False,
):
    symbol = listing["symbol"]

    if not force_refresh:
        cached = get_cached_security(
            symbol,
            ttl_hours=SECURITY_TTL_HOURS,
        )

        if cached:
            return cached, True

    coingecko = raw_research.get(
        "coingecko",
        {},
    )

    if not coingecko:
        security = empty_security()

        save_cached_security(
            symbol,
            security,
        )

        return security, False

    try:
        security_raw = (
            collect_token_security(
                coingecko
            )
        )

        security = analyze_token_security(
            security_raw
        )

        save_cached_security(
            symbol,
            security,
        )

        return security, False

    except Exception as exc:
        save_security_error(
            symbol,
            exc,
        )

        security = empty_security()
        security["warnings"] = [
            f"Ошибка Security: {str(exc)[:200]}"
        ]

        return security, False


def collect_live_launch_behavior(
    listing,
):
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

    try:
        oi_history = (
            client.open_interest_history(
                symbol,
                period="1h",
                limit=24,
            )
        )
    except Exception:
        oi_history = []

    try:
        premium = client.premium_index(
            symbol
        )

        current_funding = premium.get(
            "lastFundingRate"
        )
    except Exception:
        current_funding = None

    return analyze_launch_behavior(
        klines_5m=klines_5m,
        klines_1h=klines_1h,
        oi_history=oi_history,
        current_funding=current_funding,
    )


def calculate_buy_readiness(
    alpha,
    launch,
):
    alpha_score = float(
        alpha.get("alphaScore", 0) or 0
    )

    launch_available = bool(
        launch.get("available")
    )

    launch_score = float(
        launch.get("score", 0) or 0
    )

    alpha_hard_reject = bool(
        alpha.get("hardReject")
    )

    launch_hard_reject = bool(
        launch.get("hardReject")
    )

    if launch_available:
        score = round(
            alpha_score * 0.65
            + launch_score * 0.35
        )
    else:
        score = round(
            alpha_score * 0.65
        )

    hard_reject = (
        alpha_hard_reject
        or launch_hard_reject
    )

    if hard_reject:
        score = min(score, 49)

        action = "REJECT"
        label = "🔴 НЕ ПОКУПАТЬ"

    elif (
        score >= 80
        and launch_score >= 70
    ):
        action = "READY_FOR_ENTRY_REVIEW"
        label = "🟢 РАССМАТРИВАТЬ ВХОД"

    elif score >= 65:
        action = "WAIT_FOR_SETUP"
        label = "🟡 ЖДАТЬ СЕТАП"

    else:
        action = "SKIP"
        label = "🔴 ПРОПУСТИТЬ"

    return {
        "score": score,
        "action": action,
        "actionLabel": label,
        "hardReject": hard_reject,
        "alphaWeight": 0.65,
        "launchWeight": 0.35,
    }


def analyze_listing_cached(
    listing,
    force_research_refresh=False,
    force_security_refresh=False,
):
    symbol = listing["symbol"]

    try:
        research_bundle, research_from_cache = (
            collect_research_with_cache(
                listing,
                force_refresh=(
                    force_research_refresh
                ),
            )
        )

        raw_research = research_bundle.get(
            "raw",
            {},
        )

        research = research_bundle.get(
            "analysis",
            {},
        )

        security, security_from_cache = (
            collect_security_with_cache(
                listing,
                raw_research,
                force_refresh=(
                    force_security_refresh
                ),
            )
        )

        launch_behavior = (
            collect_live_launch_behavior(
                listing
            )
        )

        alpha = calculate_alpha_score(
            listing=listing,
            research=research,
            security=security,
        )

        buy_readiness = (
            calculate_buy_readiness(
                alpha,
                launch_behavior,
            )
        )

        base_result = (
            evaluate_listing_candidate(
                listing,
                research,
            )
        )

        base_result["security"] = security
        base_result["launchBehavior"] = (
            launch_behavior
        )
        base_result["alpha"] = alpha
        base_result["buyReadiness"] = (
            buy_readiness
        )

        base_result["alphaScore"] = (
            alpha.get("alphaScore", 0)
        )

        base_result["opportunityScore"] = (
            buy_readiness.get("score", 0)
        )

        alpha_passed = (
            alpha.get("action")
            in [
                "HIGH_PRIORITY_WATCH",
                "WATCH_FOR_ENTRY",
            ]
            and not alpha.get(
                "hardReject",
                False,
            )
        )

        launch_passed = (
            launch_behavior.get(
                "available"
            )
            and launch_behavior.get(
                "score",
                0,
            ) >= 60
            and launch_behavior.get(
                "action"
            )
            in [
                "READY_FOR_TECHNICAL_REVIEW",
                "WAIT_FOR_SETUP",
            ]
            and not launch_behavior.get(
                "hardReject",
                False,
            )
        )

        readiness_passed = (
            buy_readiness.get("action")
            in [
                "READY_FOR_ENTRY_REVIEW",
                "WAIT_FOR_SETUP",
            ]
            and not buy_readiness.get(
                "hardReject",
                False,
            )
        )

        base_result["interesting"] = (
            alpha_passed
            and launch_passed
            and readiness_passed
        )

        base_result["cacheInfo"] = {
            "researchFromCache": (
                research_from_cache
            ),
            "securityFromCache": (
                security_from_cache
            ),
            "launchUpdatedLive": True,
        }

        if alpha.get("reasonsFor"):
            base_result["reasonsFor"] = (
                alpha["reasonsFor"]
            )

        reasons_against = list(
            alpha.get(
                "reasonsAgainst",
                [],
            )
        )

        if not launch_passed:
            reasons_against.insert(
                0,
                (
                    "Launch Behavior не прошёл: "
                    f"{launch_behavior.get('actionLabel')}, "
                    f"score={launch_behavior.get('score', 0)}"
                ),
            )

        base_result["reasonsAgainst"] = (
            reasons_against[:10]
        )

        save_latest_result(
            symbol,
            base_result,
        )

        return base_result

    except Exception as exc:
        result = {
            "interesting": False,
            "listing": listing,
            "research": {
                "available": False,
                "error": str(exc),
            },
            "security": empty_security(),
            "launchBehavior": {
                "available": False,
                "score": 0,
                "action": (
                    "INSUFFICIENT_DATA"
                ),
                "actionLabel": (
                    "⚪ Недостаточно данных"
                ),
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
            "buyReadiness": {
                "score": 0,
                "action": "SKIP",
                "actionLabel": (
                    "🔴 ПРОПУСТИТЬ"
                ),
                "hardReject": False,
            },
            "reasonsAgainst": [
                (
                    "Ошибка анализа: "
                    f"{str(exc)[:300]}"
                )
            ],
        }

        save_latest_result(
            symbol,
            result,
        )

        return result