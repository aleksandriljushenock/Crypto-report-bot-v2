import os
import re
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv


load_dotenv()

COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
DEFILLAMA_BASE_URL = "https://api.llama.fi"
GITHUB_BASE_URL = "https://api.github.com"

REQUEST_TIMEOUT = 20


def safe_float(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value, default=None):
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def request_json(
    url,
    params=None,
    headers=None,
    timeout=REQUEST_TIMEOUT,
):
    response = requests.get(
        url,
        params=params,
        headers=headers,
        timeout=timeout,
    )

    response.raise_for_status()
    return response.json()


def coingecko_headers():
    api_key = os.getenv("COINGECKO_API_KEY")

    headers = {
        "accept": "application/json",
    }

    if api_key:
        headers["x-cg-demo-api-key"] = api_key

    return headers


def github_headers():
    token = os.getenv("GITHUB_TOKEN")

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Crypto-Research-Service",
    }

    if token:
        headers["Authorization"] = f"Bearer {token}"

    return headers


def normalize_symbol(symbol):
    value = symbol.upper().strip()

    suffixes = (
        "USDT",
        "USDC",
        "FDUSD",
        "BUSD",
        "USD",
        "BTC",
        "ETH",
    )

    for suffix in suffixes:
        if value.endswith(suffix) and len(value) > len(suffix):
            value = value[:-len(suffix)]
            break

    return value


def extract_github_repository(url):
    if not url:
        return None

    try:
        parsed = urlparse(url)
    except ValueError:
        return None

    if parsed.netloc.lower() not in (
        "github.com",
        "www.github.com",
    ):
        return None

    parts = [
        part
        for part in parsed.path.strip("/").split("/")
        if part
    ]

    if len(parts) < 2:
        return None

    owner = parts[0]
    repository = re.sub(r"\.git$", "", parts[1])

    if not owner or not repository:
        return None

    return {
        "owner": owner,
        "repository": repository,
        "fullName": f"{owner}/{repository}",
    }


def search_coingecko_coin(symbol, project_name=None):
    normalized_symbol = normalize_symbol(symbol)

    query = project_name or normalized_symbol

    data = request_json(
        f"{COINGECKO_BASE_URL}/search",
        params={"query": query},
        headers=coingecko_headers(),
    )

    coins = data.get("coins", [])

    if not coins:
        return None

    exact_symbol_matches = [
        item
        for item in coins
        if item.get("symbol", "").upper() == normalized_symbol
    ]

    candidates = exact_symbol_matches or coins

    candidates.sort(
        key=lambda item: (
            item.get("market_cap_rank") is None,
            item.get("market_cap_rank") or 10**9,
        )
    )

    selected = candidates[0]

    return {
        "id": selected.get("id"),
        "name": selected.get("name"),
        "symbol": selected.get("symbol", "").upper(),
        "marketCapRank": selected.get("market_cap_rank"),
        "thumb": selected.get("thumb"),
    }


def get_coingecko_details(coin_id):
    if not coin_id:
        return None

    data = request_json(
        f"{COINGECKO_BASE_URL}/coins/{coin_id}",
        params={
            "localization": "false",
            "tickers": "false",
            "market_data": "true",
            "community_data": "true",
            "developer_data": "true",
            "sparkline": "false",
        },
        headers=coingecko_headers(),
    )

    market = data.get("market_data", {})
    links = data.get("links", {})
    community = data.get("community_data", {})
    developer = data.get("developer_data", {})

    current_price = market.get("current_price", {}).get("usd")
    market_cap = market.get("market_cap", {}).get("usd")
    fdv = market.get("fully_diluted_valuation", {}).get("usd")
    total_volume = market.get("total_volume", {}).get("usd")

    circulating_supply = market.get("circulating_supply")
    total_supply = market.get("total_supply")
    max_supply = market.get("max_supply")

    homepage = next(
        (
            url
            for url in links.get("homepage", [])
            if url
        ),
        None,
    )

    blockchain_sites = [
        url
        for url in links.get("blockchain_site", [])
        if url
    ]

    github_urls = []

    repos_url = links.get("repos_url", {})

    for url in repos_url.get("github", []) or []:
        if url:
            github_urls.append(url)

    twitter_screen_name = links.get("twitter_screen_name")

    description = data.get("description", {}).get("en", "")
    platforms = data.get("platforms", {}) or {}

    contract_addresses = {
        platform: address
        for platform, address in platforms.items()
        if address
    }
    return {
        "source": "CoinGecko",
        "id": data.get("id"),
        "symbol": data.get("symbol", "").upper(),
        "name": data.get("name"),
        "assetPlatformId": data.get("asset_platform_id"),
        "contractAddresses": contract_addresses,
        "categories": data.get("categories", []),
        "genesisDate": data.get("genesis_date"),
        "marketCapRank": data.get("market_cap_rank"),
        "description": description,
        "homepage": homepage,
        "blockchainSites": blockchain_sites[:5],
        "githubUrls": github_urls[:10],
        "twitterScreenName": twitter_screen_name,
        "market": {
            "priceUsd": safe_float(current_price),
            "marketCapUsd": safe_float(market_cap),
            "fullyDilutedValuationUsd": safe_float(fdv),
            "volume24hUsd": safe_float(total_volume),
            "circulatingSupply": safe_float(circulating_supply),
            "totalSupply": safe_float(total_supply),
            "maxSupply": safe_float(max_supply),
            "athUsd": safe_float(
                market.get("ath", {}).get("usd")
            ),
            "athChangePercent": safe_float(
                market.get("ath_change_percentage", {}).get("usd")
            ),
            "priceChange24hPercent": safe_float(
                market.get("price_change_percentage_24h")
            ),
            "priceChange7dPercent": safe_float(
                market.get("price_change_percentage_7d")
            ),
            "priceChange30dPercent": safe_float(
                market.get("price_change_percentage_30d")
            ),
        },
        "community": {
            "twitterFollowers": safe_int(
                community.get("twitter_followers")
            ),
            "redditSubscribers": safe_int(
                community.get("reddit_subscribers")
            ),
            "telegramUsers": safe_int(
                community.get("telegram_channel_user_count")
            ),
        },
        "developer": {
            "forks": safe_int(developer.get("forks")),
            "stars": safe_int(developer.get("stars")),
            "subscribers": safe_int(
                developer.get("subscribers")
            ),
            "totalIssues": safe_int(
                developer.get("total_issues")
            ),
            "closedIssues": safe_int(
                developer.get("closed_issues")
            ),
            "pullRequestsMerged": safe_int(
                developer.get("pull_requests_merged")
            ),
            "commitCount4Weeks": safe_int(
                developer.get(
                    "commit_count_4_weeks"
                )
            ),
        },
    }


def get_defillama_protocols():
    return request_json(
        f"{DEFILLAMA_BASE_URL}/protocols"
    )


def match_defillama_protocol(
    protocols,
    symbol,
    project_name=None,
):
    normalized_symbol = normalize_symbol(symbol)
    normalized_name = (project_name or "").strip().lower()

    exact_symbol = []

    for protocol in protocols:
        protocol_symbol = str(
            protocol.get("symbol") or ""
        ).upper()

        if protocol_symbol == normalized_symbol:
            exact_symbol.append(protocol)

    candidates = exact_symbol

    if not candidates and normalized_name:
        candidates = [
            protocol
            for protocol in protocols
            if str(protocol.get("name") or "").lower()
            == normalized_name
        ]

    if not candidates:
        return None

    candidates.sort(
        key=lambda protocol: safe_float(
            protocol.get("tvl"),
            0.0,
        ),
        reverse=True,
    )

    protocol = candidates[0]

    return {
        "source": "DefiLlama",
        "id": protocol.get("id"),
        "slug": protocol.get("slug"),
        "name": protocol.get("name"),
        "symbol": protocol.get("symbol"),
        "category": protocol.get("category"),
        "chains": protocol.get("chains", []),
        "tvlUsd": safe_float(protocol.get("tvl")),
        "change1d": safe_float(protocol.get("change_1d")),
        "change7d": safe_float(protocol.get("change_7d")),
        "change1m": safe_float(protocol.get("change_1m")),
        "marketCapUsd": safe_float(protocol.get("mcap")),
        "marketCapToTvl": safe_float(
            protocol.get("mcap")
        ) / safe_float(protocol.get("tvl"))
        if safe_float(protocol.get("tvl"), 0) > 0
        and safe_float(protocol.get("mcap")) is not None
        else None,
        "url": protocol.get("url"),
        "twitter": protocol.get("twitter"),
        "github": protocol.get("github"),
        "description": protocol.get("description"),
    }


def get_github_repository(owner, repository):
    data = request_json(
        f"{GITHUB_BASE_URL}/repos/{owner}/{repository}",
        headers=github_headers(),
    )

    return {
        "source": "GitHub",
        "fullName": data.get("full_name"),
        "description": data.get("description"),
        "url": data.get("html_url"),
        "homepage": data.get("homepage"),
        "createdAt": data.get("created_at"),
        "updatedAt": data.get("updated_at"),
        "pushedAt": data.get("pushed_at"),
        "stars": safe_int(
            data.get("stargazers_count"),
            0,
        ),
        "forks": safe_int(data.get("forks_count"), 0),
        "openIssues": safe_int(
            data.get("open_issues_count"),
            0,
        ),
        "watchers": safe_int(
            data.get("subscribers_count"),
            0,
        ),
        "language": data.get("language"),
        "archived": bool(data.get("archived")),
        "disabled": bool(data.get("disabled")),
        "isFork": bool(data.get("fork")),
        "defaultBranch": data.get("default_branch"),
        "license": (
            data.get("license", {}).get("spdx_id")
            if data.get("license")
            else None
        ),
    }


def get_github_contributors(owner, repository):
    try:
        data = request_json(
            (
                f"{GITHUB_BASE_URL}/repos/"
                f"{owner}/{repository}/contributors"
            ),
            params={
                "per_page": 100,
                "anon": "true",
            },
            headers=github_headers(),
        )

        return {
            "countReturned": len(data),
            "topContributors": [
                {
                    "login": item.get("login"),
                    "contributions": safe_int(
                        item.get("contributions"),
                        0,
                    ),
                }
                for item in data[:10]
            ],
        }

    except requests.RequestException as exc:
        return {
            "error": str(exc),
        }


def collect_github_data(github_urls):
    repositories = []

    for url in github_urls[:5]:
        parsed = extract_github_repository(url)

        if not parsed:
            continue

        try:
            repository = get_github_repository(
                parsed["owner"],
                parsed["repository"],
            )

            contributors = get_github_contributors(
                parsed["owner"],
                parsed["repository"],
            )

            repository["contributors"] = contributors
            repositories.append(repository)

        except requests.RequestException as exc:
            repositories.append({
                "fullName": parsed["fullName"],
                "error": str(exc),
            })

    repositories.sort(
        key=lambda item: item.get("stars", 0),
        reverse=True,
    )

    return repositories


def collect_project_research(symbol, project_name=None):
    search_result = search_coingecko_coin(
        symbol=symbol,
        project_name=project_name,
    )

    if not search_result:
        return {
            "symbol": normalize_symbol(symbol),
            "available": False,
            "error": "Монета не найдена в CoinGecko",
        }

    details = get_coingecko_details(
        search_result["id"]
    )

    defi_protocol = None

    try:
        protocols = get_defillama_protocols()

        defi_protocol = match_defillama_protocol(
            protocols=protocols,
            symbol=symbol,
            project_name=details.get("name"),
        )

    except requests.RequestException as exc:
        defi_protocol = {
            "error": str(exc),
        }

    github_data = collect_github_data(
        details.get("githubUrls", [])
    )

    return {
        "available": True,
        "requestedSymbol": normalize_symbol(symbol),
        "identity": search_result,
        "coingecko": details,
        "defillama": defi_protocol,
        "github": github_data,
        "dataWarnings": [],
    }