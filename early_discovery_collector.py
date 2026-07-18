import hashlib
import os

import requests
from dotenv import load_dotenv

from early_discovery_database import (
    load_source_snapshot,
    save_source_snapshot,
)
from exchange_announcement_sources import (
    collect_exchange_sources,
)


load_dotenv()


COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
CMC_BASE_URL = "https://pro-api.coinmarketcap.com"

REQUEST_TIMEOUT = 30


def request_json(
    url,
    params=None,
    headers=None,
):
    response = requests.get(
        url,
        params=params,
        headers=headers,
        timeout=(7, REQUEST_TIMEOUT),
    )

    response.raise_for_status()
    return response.json()


def coingecko_headers():
    headers = {
        "accept": "application/json",
        "user-agent": "Crypto-Early-Discovery",
    }

    api_key = os.getenv("COINGECKO_API_KEY")

    if api_key:
        headers["x-cg-demo-api-key"] = api_key

    return headers


def cmc_headers():
    api_key = os.getenv(
        "COINMARKETCAP_API_KEY"
    )

    if not api_key:
        return None

    return {
        "Accept": "application/json",
        "X-CMC_PRO_API_KEY": api_key,
        "User-Agent": "Crypto-Early-Discovery",
    }


def select_contract(platforms):
    if not isinstance(platforms, dict):
        return None, None

    priority = [
        "ethereum",
        "solana",
        "binance-smart-chain",
        "base",
        "arbitrum-one",
        "sui",
        "polygon-pos",
        "avalanche",
    ]

    for platform in priority:
        address = platforms.get(platform)

        if address:
            return platform, address

    for platform, address in platforms.items():
        if address:
            return platform, address

    return None, None


def collect_coingecko_new():
    data = request_json(
        f"{COINGECKO_BASE_URL}/coins/list",
        params={
            "include_platform": "true",
        },
        headers=coingecko_headers(),
    )

    current_map = {
        item.get("id"): item
        for item in data
        if isinstance(item, dict)
        and item.get("id")
    }

    previous = load_source_snapshot(
        "COINGECKO"
    )

    current_ids = set(current_map)

    if not previous:
        save_source_snapshot(
            "COINGECKO",
            sorted(current_ids),
        )

        return {
            "items": [],
            "initialized": True,
            "message": (
                "Создан первый снимок CoinGecko. "
                "Новые монеты появятся со следующего запуска."
            ),
        }

    previous_ids = set(previous)
    new_ids = current_ids - previous_ids

    items = []

    for coin_id in new_ids:
        item = current_map[coin_id]

        platform, address = select_contract(
            item.get("platforms", {})
        )

        items.append({
            "source": "COINGECKO",
            "externalId": coin_id,
            "symbol": str(
                item.get("symbol") or ""
            ).upper(),
            "projectName": item.get("name"),
            "slug": coin_id,
            "contractPlatform": platform,
            "contractAddress": address,
            "announcementUrl": (
                "https://www.coingecko.com/en/coins/"
                f"{coin_id}"
            ),
            "sourceAddedAt": None,
        })

    save_source_snapshot(
        "COINGECKO",
        sorted(current_ids),
    )

    return {
        "items": items,
        "initialized": False,
    }


def collect_cmc_new(limit=100):
    headers = cmc_headers()

    if not headers:
        return {
            "items": [],
            "skipped": True,
            "error": (
                "COINMARKETCAP_API_KEY не задан"
            ),
        }

    try:
        data = request_json(
            f"{CMC_BASE_URL}/v1/cryptocurrency/listings/new",
            params={
                "start": 1,
                "limit": limit,
                "convert": "USD",
            },
            headers=headers,
        )

    except requests.HTTPError as exc:
        status = (
            exc.response.status_code
            if exc.response is not None
            else None
        )

        return {
            "items": [],
            "skipped": True,
            "error": (
                "CoinMarketCap listings/new "
                f"недоступен: HTTP {status}. "
                "Проверь API-план."
            ),
        }

    items = []

    for item in data.get("data", []):
        platform = item.get("platform") or {}

        external_id = str(
            item.get("id")
            or item.get("slug")
        )

        items.append({
            "source": "COINMARKETCAP",
            "externalId": external_id,
            "symbol": str(
                item.get("symbol") or ""
            ).upper(),
            "projectName": item.get("name"),
            "slug": item.get("slug"),
            "contractPlatform": (
                platform.get("name")
                or platform.get("slug")
            ),
            "contractAddress": (
                platform.get("token_address")
            ),
            "announcementUrl": (
                "https://coinmarketcap.com/currencies/"
                f"{item.get('slug')}/"
            ),
            "sourceAddedAt": item.get(
                "date_added"
            ),
        })

    return {
        "items": items,
        "skipped": False,
    }


def collect_exchange_announcements():
    return collect_exchange_sources()


def collect_all_discovery_sources():
    items = []
    source_status = []

    try:
        coingecko = collect_coingecko_new()

        items.extend(
            coingecko.get("items", [])
        )

        source_status.append({
            "source": "COINGECKO",
            "count": len(
                coingecko.get("items", [])
            ),
            "message": coingecko.get(
                "message"
            ),
        })

    except Exception as exc:
        source_status.append({
            "source": "COINGECKO",
            "count": 0,
            "error": str(exc),
        })

    try:
        cmc = collect_cmc_new(limit=100)

        items.extend(
            cmc.get("items", [])
        )

        source_status.append({
            "source": "COINMARKETCAP",
            "count": len(
                cmc.get("items", [])
            ),
            "error": cmc.get("error"),
        })

    except Exception as exc:
        source_status.append({
            "source": "COINMARKETCAP",
            "count": 0,
            "error": str(exc),
        })

    try:
        announcements = collect_exchange_announcements()
        items.extend(announcements.get("items", []))
        source_status.extend(announcements.get("sources", []))

    except Exception as exc:
        source_status.append({
            "source": "EXCHANGES",
            "count": 0,
            "error": str(exc),
        })

    unique = {}

    for item in items:
        key = (
            item.get("source"),
            str(item.get("externalId")),
        )
        unique[key] = item

    return {
        "items": list(unique.values()),
        "sources": source_status,
    }