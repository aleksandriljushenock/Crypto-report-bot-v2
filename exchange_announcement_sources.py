"""Stable exchange discovery using public market APIs.

The old implementation scraped announcement HTML pages and frequently failed with
403/404 responses. This module discovers newly-added spot markets by comparing
public exchange instrument lists with a persistent snapshot. Where an exchange
publishes an opening/listing timestamp, recent markets are also returned during
bootstrap so the first run is useful without flooding the database.
"""

from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable

import requests

from early_discovery_database import load_source_snapshot, save_source_snapshot

REQUEST_CONNECT_TIMEOUT = float(os.getenv("DISCOVERY_CONNECT_TIMEOUT", "7"))
REQUEST_READ_TIMEOUT = float(os.getenv("DISCOVERY_READ_TIMEOUT", "30"))
MAX_AGE_DAYS = int(os.getenv("DISCOVERY_RECENT_DAYS", "45"))
MAX_RETRIES = int(os.getenv("DISCOVERY_HTTP_RETRIES", "3"))

STABLE_BASES = {
    "USDT", "USDC", "FDUSD", "TUSD", "USDE", "DAI", "USDP", "BUSD",
    "EUR", "EURT", "USD", "PYUSD", "GUSD", "USDD", "USTC",
}
QUOTE_PRIORITY = ("USDT", "USDC", "USD")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _to_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        result = int(float(value))
        # Seconds -> milliseconds.
        if result and result < 10_000_000_000:
            result *= 1000
        return result
    except (TypeError, ValueError, OverflowError):
        return None


def _iso_from_ms(value: Any) -> str | None:
    timestamp = _to_int(value)
    if not timestamp:
        return None
    try:
        return datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def _is_recent(value: Any) -> bool:
    timestamp = _to_int(value)
    if not timestamp:
        return False
    age_ms = _now_ms() - timestamp
    return 0 <= age_ms <= MAX_AGE_DAYS * 86_400_000


def _external_id(exchange: str, symbol: str) -> str:
    return hashlib.sha256(f"{exchange}|SPOT|{symbol}".encode("utf-8")).hexdigest()


def _request_json(url: str, params: dict[str, Any] | None = None) -> Any:
    last_error: Exception | None = None
    headers = {
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": "Crypto-Discovery-Service/6.0",
        "Connection": "close",
    }

    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=(REQUEST_CONNECT_TIMEOUT, REQUEST_READ_TIMEOUT),
            )
            if response.status_code == 429:
                time.sleep(2 + attempt * 2)
                continue
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt + 1 < MAX_RETRIES:
                time.sleep(1 + attempt)

    raise RuntimeError(f"API request failed: {url} | {last_error}") from last_error


def _market(
    exchange: str,
    symbol: str,
    base: str,
    quote: str,
    *,
    list_time: Any = None,
    project_name: str | None = None,
) -> dict[str, Any] | None:
    symbol = str(symbol or "").upper().strip()
    base = str(base or "").upper().strip()
    quote = str(quote or "").upper().strip()
    if not symbol or not base or quote not in QUOTE_PRIORITY or base in STABLE_BASES:
        return None

    return {
        "exchange": exchange,
        "symbol": symbol,
        "base": base,
        "quote": quote,
        "projectName": project_name or base,
        "listTimeMs": _to_int(list_time),
    }


def _parse_binance(data: Any) -> list[dict[str, Any]]:
    result = []
    for row in (data or {}).get("symbols", []):
        if row.get("status") != "TRADING" or not row.get("isSpotTradingAllowed", True):
            continue
        item = _market("BINANCE", row.get("symbol"), row.get("baseAsset"), row.get("quoteAsset"))
        if item:
            result.append(item)
    return result


def _parse_bybit(data: Any) -> list[dict[str, Any]]:
    result = []
    for row in ((data or {}).get("result") or {}).get("list", []):
        if row.get("status") not in ("Trading", "PreLaunch"):
            continue
        item = _market(
            "BYBIT", row.get("symbol"), row.get("baseCoin"), row.get("quoteCoin"),
            list_time=row.get("launchTime"),
        )
        if item:
            result.append(item)
    return result


def _parse_okx(data: Any) -> list[dict[str, Any]]:
    result = []
    for row in (data or {}).get("data", []):
        if row.get("state") not in ("live", "preopen"):
            continue
        item = _market(
            "OKX", row.get("instId"), row.get("baseCcy"), row.get("quoteCcy"),
            list_time=row.get("listTime"),
        )
        if item:
            result.append(item)
    return result


def _parse_kucoin(data: Any) -> list[dict[str, Any]]:
    result = []
    for row in (data or {}).get("data", []):
        if not row.get("enableTrading", False):
            continue
        item = _market(
            "KUCOIN", row.get("symbol"), row.get("baseCurrency"), row.get("quoteCurrency"),
            list_time=row.get("tradingStartTime"),
        )
        if item:
            result.append(item)
    return result


def _parse_gate(data: Any) -> list[dict[str, Any]]:
    result = []
    for row in data if isinstance(data, list) else []:
        if row.get("trade_status") not in ("tradable", "buyable", None):
            continue
        item = _market(
            "GATE", row.get("id"), row.get("base"), row.get("quote"),
            list_time=row.get("buy_start") or row.get("sell_start"),
        )
        if item:
            result.append(item)
    return result


def _parse_bitget(data: Any) -> list[dict[str, Any]]:
    result = []
    for row in (data or {}).get("data", []):
        if row.get("status") not in ("online", "gray"):
            continue
        item = _market(
            "BITGET", row.get("symbol"), row.get("baseCoin"), row.get("quoteCoin"),
            list_time=row.get("openTime"),
        )
        if item:
            result.append(item)
    return result


def _parse_mexc(data: Any) -> list[dict[str, Any]]:
    result = []
    for row in (data or {}).get("symbols", []):
        if str(row.get("status", "")).upper() not in ("1", "ENABLED", "TRADING", ""):
            continue
        item = _market("MEXC", row.get("symbol"), row.get("baseAsset"), row.get("quoteAsset"))
        if item:
            result.append(item)
    return result


def _parse_htx(data: Any) -> list[dict[str, Any]]:
    rows = (data or {}).get("data", [])
    if isinstance(rows, dict):
        rows = rows.get("symbols") or rows.get("list") or []
    result = []
    for row in rows:
        state = str(row.get("state") or row.get("trade-status") or row.get("status") or "online").lower()
        if state not in ("online", "enabled", "1", "normal"):
            continue
        base = row.get("base-currency") or row.get("bc") or row.get("baseCurrency")
        quote = row.get("quote-currency") or row.get("qc") or row.get("quoteCurrency")
        symbol = row.get("symbol") or f"{base}{quote}"
        item = _market("HTX", symbol, base, quote, list_time=row.get("open-time") or row.get("openTime"))
        if item:
            result.append(item)
    return result


def _parse_bingx(data: Any) -> list[dict[str, Any]]:
    rows = (data or {}).get("data", {})
    if isinstance(rows, dict):
        rows = rows.get("symbols") or rows.get("list") or []
    result = []
    for row in rows if isinstance(rows, list) else []:
        status = str(row.get("status", "1")).lower()
        if status not in ("1", "online", "trading", "true"):
            continue
        symbol = row.get("symbol")
        base = row.get("baseAsset") or row.get("baseCoin")
        quote = row.get("quoteAsset") or row.get("quoteCoin")
        if (not base or not quote) and isinstance(symbol, str) and "-" in symbol:
            base, quote = symbol.split("-", 1)
        item = _market("BINGX", symbol, base, quote, list_time=row.get("openTime"))
        if item:
            result.append(item)
    return result


def _parse_coinex(data: Any) -> list[dict[str, Any]]:
    rows = (data or {}).get("data", [])
    result = []
    for row in rows if isinstance(rows, list) else []:
        status = str(row.get("status") or row.get("is_trading_available") or "online").lower()
        if status in ("offline", "false", "0", "delisted"):
            continue
        symbol = row.get("market") or row.get("symbol")
        base = row.get("base_ccy") or row.get("base_currency") or row.get("base")
        quote = row.get("quote_ccy") or row.get("quote_currency") or row.get("quote")
        item = _market("COINEX", symbol, base, quote, list_time=row.get("created_at"))
        if item:
            result.append(item)
    return result


def _parse_lbank(data: Any) -> list[dict[str, Any]]:
    rows = (data or {}).get("data", [])
    result = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, str):
            text = row.upper()
            if "_" in text:
                base, quote = text.split("_", 1)
            elif text.endswith("USDT"):
                base, quote = text[:-4], "USDT"
            else:
                continue
            item = _market("LBANK", text, base, quote)
        elif isinstance(row, dict):
            symbol = row.get("symbol") or row.get("pair")
            base = row.get("baseAsset") or row.get("base")
            quote = row.get("quoteAsset") or row.get("quote")
            item = _market("LBANK", symbol, base, quote, list_time=row.get("openTime"))
        else:
            item = None
        if item:
            result.append(item)
    return result


# URL fallbacks are tried in order. They are all public market-data endpoints.
SOURCES: list[dict[str, Any]] = [
    {"name": "BINANCE", "urls": [("https://api.binance.com/api/v3/exchangeInfo", None), ("https://api1.binance.com/api/v3/exchangeInfo", None)], "parser": _parse_binance},
    {"name": "BYBIT", "urls": [("https://api.bybit.com/v5/market/instruments-info", {"category": "spot"})], "parser": _parse_bybit},
    {"name": "OKX", "urls": [("https://openapi.okx.com/api/v5/public/instruments", {"instType": "SPOT"}), ("https://www.okx.com/api/v5/public/instruments", {"instType": "SPOT"})], "parser": _parse_okx},
    {"name": "KUCOIN", "urls": [("https://api.kucoin.com/api/v2/symbols", None)], "parser": _parse_kucoin},
    {"name": "GATE", "urls": [("https://api.gateio.ws/api/v4/spot/currency_pairs", None), ("https://api.gateeu.com/api/v4/spot/currency_pairs", None)], "parser": _parse_gate},
    {"name": "BITGET", "urls": [("https://api.bitget.com/api/v2/spot/public/symbols", None)], "parser": _parse_bitget},
    {"name": "MEXC", "urls": [("https://api.mexc.com/api/v3/exchangeInfo", None)], "parser": _parse_mexc},
    {"name": "HTX", "urls": [("https://api.huobi.pro/v1/settings/common/market-symbols", None), ("https://api.huobi.pro/v1/common/symbols", None)], "parser": _parse_htx},
    {"name": "BINGX", "urls": [("https://open-api.bingx.com/openApi/spot/v1/common/symbols", None)], "parser": _parse_bingx},
    {"name": "COINEX", "urls": [("https://api.coinex.com/v2/spot/market", None)], "parser": _parse_coinex},
    {"name": "LBANK", "urls": [("https://api.lbkex.com/v2/currencyPairs.do", None)], "parser": _parse_lbank},
]


def _fetch_source(source: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    errors = []
    for url, params in source["urls"]:
        try:
            data = _request_json(url, params)
            markets = source["parser"](data)
            return markets, url
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    raise RuntimeError(" | ".join(errors))


def _build_discovery_item(market: dict[str, Any]) -> dict[str, Any]:
    exchange = market["exchange"]
    symbol = market["symbol"]
    base = market["base"]
    return {
        "source": exchange,
        "externalId": _external_id(exchange, symbol),
        "symbol": base,
        "tradingPair": symbol,
        "projectName": market.get("projectName") or base,
        "slug": None,
        "contractPlatform": None,
        "contractAddress": None,
        "announcementUrl": None,
        "sourceAddedAt": _iso_from_ms(market.get("listTimeMs")),
        "discoveryMethod": "OFFICIAL_MARKET_API",
    }


def collect_exchange_sources() -> dict[str, Any]:
    """Collect newly added spot markets from public exchange APIs.

    First run behavior:
    * saves a baseline snapshot;
    * still returns markets with an explicit listing time within MAX_AGE_DAYS.
    Later runs return only symbols not present in the previous successful snapshot.
    A failed source never overwrites its previous snapshot.
    """

    all_items: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []

    for source in SOURCES:
        name = source["name"]
        snapshot_key = f"EXCHANGE_MARKETS_V6:{name}"
        try:
            markets, endpoint = _fetch_source(source)
            current_map = {market["symbol"]: market for market in markets}
            current_symbols = set(current_map)
            previous = load_source_snapshot(snapshot_key)
            previous_symbols = set(previous or [])

            if previous is None:
                selected = [market for market in markets if _is_recent(market.get("listTimeMs"))]
                initialized = True
            else:
                new_symbols = current_symbols - previous_symbols
                selected = [current_map[symbol] for symbol in sorted(new_symbols)]
                initialized = False

            # Save only after a valid response was parsed.
            save_source_snapshot(snapshot_key, sorted(current_symbols))
            items = [_build_discovery_item(market) for market in selected]
            all_items.extend(items)
            statuses.append({
                "source": name,
                "count": len(items),
                "markets": len(markets),
                "initialized": initialized,
                "method": "API",
                "endpoint": endpoint,
            })
        except Exception as exc:
            statuses.append({
                "source": name,
                "count": 0,
                "method": "API",
                "error": str(exc),
            })

    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for item in all_items:
        unique[(item["source"], item["externalId"])] = item

    return {"items": list(unique.values()), "sources": statuses}
