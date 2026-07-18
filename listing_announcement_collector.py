import hashlib
import html
import re
import time
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


REQUEST_TIMEOUT = 25


SOURCES = [
    {
        "name": "MEXC",
        "url": (
            "https://support.mexc.com/"
            "hc/en-001/sections/"
            "360000547811-New-Listings"
        ),
        "baseUrl": "https://support.mexc.com",
    },
    {
        "name": "KUCOIN",
        "url": (
            "https://www.kucoin.com/"
            "announcement/new-listings"
        ),
        "baseUrl": "https://www.kucoin.com",
    },
]


IGNORE_WORDS = {
    "FUTURES",
    "PERPETUAL",
    "CONTRACT",
    "USDT",
    "USDC",
    "BTC",
    "ETH",
    "USD",
    "MEXC",
    "KUCOIN",
    "LISTING",
    "LISTED",
    "TRADING",
    "TOKEN",
    "SPOT",
    "NEW",
    "INITIAL",
    "WORLD",
    "PREMIERE",
}


BLOCKED_TITLE_WORDS = (
    "delist",
    "postpone",
    "postponed",
    "delay",
    "suspend",
    "suspended",
    "margin adds",
    "futures new listing",
    "perpetual contract",
    "stock index",
    "convert adds",
    "trading pair opening time update",
)


POSITIVE_TITLE_PATTERNS = (
    "will list",
    "gets listed",
    "listed on",
    "initial listing",
    "world premiere",
    "spot trading",
    "new listing",
)


def normalize_text(value):
    value = html.unescape(
        value or ""
    )

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value.strip()


def make_external_key(
    source,
    url,
    title,
):
    raw = (
        source
        + "|"
        + url
        + "|"
        + title
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def request_html(url):
    last_error = None

    for attempt in range(4):
        try:
            response = requests.get(
                url,
                timeout=(6, REQUEST_TIMEOUT),
                headers={
                    "accept": (
                        "text/html,"
                        "application/xhtml+xml"
                    ),
                    "accept-language": (
                        "en-US,en;q=0.9"
                    ),
                    "user-agent": (
                        "Mozilla/5.0 "
                        "(Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 "
                        "(KHTML, like Gecko) "
                        "Chrome/126.0 Safari/537.36"
                    ),
                },
            )

            if response.status_code == 429:
                time.sleep(
                    3 + attempt * 3
                )
                continue

            response.raise_for_status()

            return response.text

        except requests.RequestException as exc:
            last_error = exc

            if attempt < 3:
                time.sleep(
                    1 + attempt
                )

    raise RuntimeError(
        f"Не удалось загрузить {url}: "
        f"{last_error}"
    )


def looks_like_spot_listing(title):
    lower = title.lower()

    if any(
        word in lower
        for word in BLOCKED_TITLE_WORDS
    ):
        return False

    return any(
        pattern in lower
        for pattern in POSITIVE_TITLE_PATTERNS
    )


def clean_project_name(value):
    value = normalize_text(value)

    prefixes = (
        "World Premiere:",
        "World Premiere -",
        "Initial Listing:",
        "Initial Listing -",
        "New Listing:",
        "New Listing -",
    )

    for prefix in prefixes:
        if value.lower().startswith(
            prefix.lower()
        ):
            value = value[len(prefix):].strip()

    value = re.sub(
        r"^\[[^\]]+\]\s*",
        "",
        value,
    )

    return normalize_text(value)

def extract_project_and_symbol(title):
    title = normalize_text(title)

    patterns = [
        (
            r"(?:will list|lists?)\s+"
            r"([A-Za-z0-9 ._'&+\-]+?)\s*"
            r"\(([A-Za-z0-9]{2,20})\)"
        ),
        (
            r"^(.+?)\s*"
            r"\(([A-Za-z0-9]{2,20})\)\s+"
            r"(?:gets listed|listed on)"
        ),
        (
            r"(?:world premiere[:\s-]*)"
            r"(.+?)\s*"
            r"\(([A-Za-z0-9]{2,20})\)"
        ),
        (
            r"(?:initial listing[^\n:–-]*"
            r"[:–-]?\s*)"
            r".*?([A-Za-z0-9 ._'&+\-]+?)\s*"
            r"\(([A-Za-z0-9]{2,20})\)"
        ),
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            title,
            re.IGNORECASE,
        )

        if not match:
            continue

        project_name = clean_project_name(
            match.group(1)
        )

        symbol = match.group(2).upper()

        if symbol in IGNORE_WORDS:
            continue

        return {
            "projectName": project_name,
            "symbol": symbol,
        }

    bracket_symbols = re.findall(
        r"\(([A-Za-z0-9]{2,20})\)",
        title,
    )

    for candidate in reversed(
        bracket_symbols
    ):
        symbol = candidate.upper()

        if symbol in IGNORE_WORDS:
            continue

        project_name = re.sub(
            rf"\s*\({re.escape(candidate)}\).*$",
            "",
            title,
            flags=re.IGNORECASE,
        )

        project_name = clean_project_name(
            project_name
        )

        return {
            "projectName": project_name,
            "symbol": symbol,
        }

    return {
        "projectName": None,
        "symbol": None,
    }

def extract_listing_datetime(text):
    text = normalize_text(text)

    patterns = [
        (
            r"(?:trading|listing)"
            r"(?:\s+starts?)?\s*[:\-]\s*"
            r"("
            r"\d{1,2}:\d{2}\s+on\s+"
            r"[A-Za-z]+\s+\d{1,2},\s+\d{4}"
            r"\s*\(UTC\)"
            r")"
        ),
        (
            r"("
            r"\d{1,2}:\d{2}\s+on\s+"
            r"[A-Za-z]+\s+\d{1,2},\s+\d{4}"
            r"\s*\(UTC\)"
            r")"
        ),
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            re.IGNORECASE,
        )

        if match:
            return normalize_text(
                match.group(1)
            )

    return None

def parse_listing_datetime(value):
    if not value:
        return None

    normalized = normalize_text(value)

    formats = (
        "%H:%M on %B %d, %Y (UTC)",
        "%H:%M on %b %d, %Y (UTC)",
    )

    for date_format in formats:
        try:
            parsed = datetime.strptime(
                normalized,
                date_format,
            )

            return parsed.replace(
                tzinfo=timezone.utc
            )

        except ValueError:
            continue

    return None


def is_recent_listing(
    listing_at,
    max_age_days=30,
):
    parsed = parse_listing_datetime(
        listing_at
    )

    if parsed is None:
        return True

    age = (
        datetime.now(timezone.utc)
        - parsed
    ).days

    return 0 <= age <= max_age_days

def parse_source(source):
    page_html = request_html(
        source["url"]
    )

    soup = BeautifulSoup(
        page_html,
        "html.parser",
    )

    results = []
    seen = set()

    for anchor in soup.find_all(
        "a",
        href=True,
    ):
        title = normalize_text(
            anchor.get_text(
                " ",
                strip=True,
            )
        )

        if len(title) < 12:
            continue

        if not looks_like_spot_listing(
            title
        ):
            continue

        href = anchor.get("href")

        if not href:
            continue

        url = urljoin(
            source["baseUrl"],
            href,
        )

        dedupe_key = (
            title.lower(),
            url,
        )

        if dedupe_key in seen:
            continue

        seen.add(dedupe_key)

        identity = (
            extract_project_and_symbol(
                title
            )
        )

        if not identity.get("symbol"):
            continue

        listing_at = (
            extract_listing_datetime(
                title
            )
        )

        if not is_recent_listing(
            listing_at,
            max_age_days=30,
        ):
            continue

        results.append({
            "source": source["name"],
            "externalKey": make_external_key(
                source["name"],
                url,
                title,
            ),
            "title": title,
            "url": url,
            "symbol": identity.get(
                "symbol"
            ),
            "projectName": identity.get(
                "projectName"
            ),
            "publishedAt": None,
            "listingAt": listing_at,
        })

    return results


def collect_listing_announcements():
    """Compatibility wrapper over the API-based Discovery Engine."""
    from exchange_announcement_sources import collect_exchange_sources

    result = collect_exchange_sources()
    announcements = []
    errors = []

    for item in result.get("items", []):
        pair = item.get("tradingPair") or item.get("symbol") or ""
        source = item.get("source") or "UNKNOWN"
        symbol = item.get("symbol")
        project_name = item.get("projectName") or symbol
        title = f"{project_name} ({symbol}) added to {source} Spot as {pair}"
        announcements.append({
            "source": source,
            "externalKey": item.get("externalId"),
            "title": title,
            "url": item.get("announcementUrl") or "",
            "symbol": symbol,
            "projectName": project_name,
            "publishedAt": None,
            "listingAt": item.get("sourceAddedAt"),
        })

    for status in result.get("sources", []):
        if status.get("error"):
            errors.append({
                "source": status.get("source"),
                "error": status.get("error"),
            })

    return {
        "announcements": announcements,
        "errors": errors,
    }
