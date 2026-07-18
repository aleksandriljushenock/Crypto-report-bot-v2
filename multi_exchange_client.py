"""Public-market aggregation across major centralized exchanges.

The module intentionally uses unauthenticated REST endpoints only. Every adapter
returns the same normalized shape so discovery code does not need exchange-
specific parsing logic.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import random
import time
from dataclasses import dataclass, asdict
from typing import Any, Iterable

import requests

LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = float(os.getenv("EXCHANGE_HTTP_TIMEOUT", "20"))
MAX_RETRIES = int(os.getenv("EXCHANGE_HTTP_RETRIES", "4"))
QUOTE_ASSETS = tuple(
    q.strip().upper()
    for q in os.getenv("EXCHANGE_QUOTES", "USDT,USDC").split(",")
    if q.strip()
)

STABLE_OR_FIAT = {
    "USDT", "USDC", "FDUSD", "TUSD", "USDE", "USDP", "DAI", "BUSD",
    "USD", "EUR", "GBP", "TRY", "BRL", "RUB", "JPY", "AUD", "CAD",
}


@dataclass(slots=True)
class MarketListing:
    exchange: str
    symbol: str
    base_asset: str
    quote_asset: str
    market_type: str = "spot"
    status: str = "TRADING"
    last_price: float = 0.0
    quote_volume_24h: float = 0.0
    price_change_24h: float = 0.0
    listed_at_ms: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class PublicExchangeClient:
    name = "exchange"
    base_url = ""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT, retries: int = MAX_RETRIES):
        self.timeout = timeout
        self.retries = max(1, retries)
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "Crypto-Report-Bot/2.0",
        })

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=(5, self.timeout),
                )
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    wait = _float(retry_after, 0.0) or min(30.0, 1.5 * (2 ** attempt))
                    time.sleep(wait + random.uniform(0.05, 0.35))
                    continue
                if 500 <= response.status_code < 600:
                    raise requests.HTTPError(
                        f"HTTP {response.status_code}", response=response
                    )
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt + 1 < self.retries:
                    time.sleep(min(15.0, 0.8 * (2 ** attempt)) + random.uniform(0.05, 0.25))
        raise RuntimeError(f"{self.name} request failed: {url} {params}: {last_error}") from last_error

    def list_markets(self) -> list[MarketListing]:
        raise NotImplementedError


class BinanceClient(PublicExchangeClient):
    name = "binance"
    base_url = "https://api.binance.com"

    def list_markets(self) -> list[MarketListing]:
        info = self.get_json("/api/v3/exchangeInfo")
        tickers = self.get_json("/api/v3/ticker/24hr")
        ticker_map = {x.get("symbol"): x for x in tickers if isinstance(x, dict)}
        result = []
        for item in info.get("symbols", []):
            base = str(item.get("baseAsset") or "").upper()
            quote = str(item.get("quoteAsset") or "").upper()
            if quote not in QUOTE_ASSETS or base in STABLE_OR_FIAT:
                continue
            status = str(item.get("status") or "")
            if status not in {"TRADING", "ENABLED"}:
                continue
            symbol = str(item.get("symbol") or "")
            t = ticker_map.get(symbol, {})
            result.append(MarketListing(
                exchange=self.name, symbol=symbol, base_asset=base, quote_asset=quote,
                status="TRADING", last_price=_float(t.get("lastPrice")),
                quote_volume_24h=_float(t.get("quoteVolume")),
                price_change_24h=_float(t.get("priceChangePercent")),
            ))
        return result


class MexcClient(BinanceClient):
    name = "mexc"
    base_url = "https://api.mexc.com"


class BybitClient(PublicExchangeClient):
    name = "bybit"
    base_url = "https://api.bybit.com"

    def list_markets(self) -> list[MarketListing]:
        instruments = self.get_json("/v5/market/instruments-info", {"category": "spot", "limit": 1000})
        tickers = self.get_json("/v5/market/tickers", {"category": "spot"})
        items = instruments.get("result", {}).get("list", [])
        tmap = {x.get("symbol"): x for x in tickers.get("result", {}).get("list", [])}
        result = []
        for item in items:
            base = str(item.get("baseCoin") or "").upper()
            quote = str(item.get("quoteCoin") or "").upper()
            if quote not in QUOTE_ASSETS or base in STABLE_OR_FIAT:
                continue
            if str(item.get("status") or "").lower() not in {"trading", "available"}:
                continue
            symbol = str(item.get("symbol") or "")
            t = tmap.get(symbol, {})
            turnover = _float(t.get("turnover24h"))
            result.append(MarketListing(
                exchange=self.name, symbol=symbol, base_asset=base, quote_asset=quote,
                last_price=_float(t.get("lastPrice")), quote_volume_24h=turnover,
                price_change_24h=_float(t.get("price24hPcnt")) * 100.0,
            ))
        return result


class OkxClient(PublicExchangeClient):
    name = "okx"
    base_url = "https://www.okx.com"

    def list_markets(self) -> list[MarketListing]:
        instruments = self.get_json("/api/v5/public/instruments", {"instType": "SPOT"})
        tickers = self.get_json("/api/v5/market/tickers", {"instType": "SPOT"})
        tmap = {x.get("instId"): x for x in tickers.get("data", [])}
        result = []
        for item in instruments.get("data", []):
            base = str(item.get("baseCcy") or "").upper()
            quote = str(item.get("quoteCcy") or "").upper()
            if quote not in QUOTE_ASSETS or base in STABLE_OR_FIAT:
                continue
            if str(item.get("state") or "").lower() != "live":
                continue
            symbol = str(item.get("instId") or "")
            t = tmap.get(symbol, {})
            last = _float(t.get("last"))
            open24 = _float(t.get("open24h"))
            change = ((last / open24) - 1.0) * 100.0 if last and open24 else 0.0
            result.append(MarketListing(
                exchange=self.name, symbol=symbol, base_asset=base, quote_asset=quote,
                last_price=last, quote_volume_24h=_float(t.get("volCcy24h")),
                price_change_24h=change,
                listed_at_ms=int(item.get("listTime")) if str(item.get("listTime") or "").isdigit() else None,
            ))
        return result


class KucoinClient(PublicExchangeClient):
    name = "kucoin"
    base_url = "https://api.kucoin.com"

    def list_markets(self) -> list[MarketListing]:
        symbols = self.get_json("/api/v2/symbols").get("data", [])
        tickers = self.get_json("/api/v1/market/allTickers").get("data", {}).get("ticker", [])
        tmap = {x.get("symbol"): x for x in tickers}
        result = []
        for item in symbols:
            base = str(item.get("baseCurrency") or "").upper()
            quote = str(item.get("quoteCurrency") or "").upper()
            if quote not in QUOTE_ASSETS or base in STABLE_OR_FIAT or not item.get("enableTrading"):
                continue
            symbol = str(item.get("symbol") or "")
            t = tmap.get(symbol, {})
            result.append(MarketListing(
                exchange=self.name, symbol=symbol, base_asset=base, quote_asset=quote,
                last_price=_float(t.get("last")), quote_volume_24h=_float(t.get("volValue")),
                price_change_24h=_float(t.get("changeRate")) * 100.0,
            ))
        return result


class GateClient(PublicExchangeClient):
    name = "gate"
    base_url = "https://api.gateio.ws/api/v4"

    def list_markets(self) -> list[MarketListing]:
        pairs = self.get_json("/spot/currency_pairs")
        tickers = self.get_json("/spot/tickers")
        tmap = {x.get("currency_pair"): x for x in tickers}
        result = []
        for item in pairs:
            base = str(item.get("base") or "").upper()
            quote = str(item.get("quote") or "").upper()
            if quote not in QUOTE_ASSETS or base in STABLE_OR_FIAT:
                continue
            if str(item.get("trade_status") or "").lower() not in {"tradable", "buyable"}:
                continue
            symbol = str(item.get("id") or "")
            t = tmap.get(symbol, {})
            result.append(MarketListing(
                exchange=self.name, symbol=symbol, base_asset=base, quote_asset=quote,
                last_price=_float(t.get("last")), quote_volume_24h=_float(t.get("quote_volume")),
                price_change_24h=_float(t.get("change_percentage")),
            ))
        return result


class BitgetClient(PublicExchangeClient):
    name = "bitget"
    base_url = "https://api.bitget.com"

    def list_markets(self) -> list[MarketListing]:
        symbols = self.get_json("/api/v2/spot/public/symbols").get("data", [])
        tickers = self.get_json("/api/v2/spot/market/tickers").get("data", [])
        tmap = {x.get("symbol"): x for x in tickers}
        result = []
        for item in symbols:
            base = str(item.get("baseCoin") or "").upper()
            quote = str(item.get("quoteCoin") or "").upper()
            if quote not in QUOTE_ASSETS or base in STABLE_OR_FIAT:
                continue
            if str(item.get("status") or "").lower() not in {"online", "trading"}:
                continue
            symbol = str(item.get("symbol") or "")
            t = tmap.get(symbol, {})
            result.append(MarketListing(
                exchange=self.name, symbol=symbol, base_asset=base, quote_asset=quote,
                last_price=_float(t.get("lastPr")), quote_volume_24h=_float(t.get("usdtVolume")),
                price_change_24h=_float(t.get("change24h")) * 100.0,
            ))
        return result


CLIENTS = {
    "binance": BinanceClient,
    "mexc": MexcClient,
    "bybit": BybitClient,
    "okx": OkxClient,
    "kucoin": KucoinClient,
    "gate": GateClient,
    "bitget": BitgetClient,
}


def configured_exchanges() -> list[str]:
    raw = os.getenv("ENABLED_EXCHANGES", ",".join(CLIENTS))
    names = []
    for name in raw.split(","):
        key = name.strip().lower()
        if key in CLIENTS and key not in names:
            names.append(key)
    return names


def collect_exchange_markets(exchange_names: Iterable[str] | None = None) -> dict[str, Any]:
    names = list(exchange_names or configured_exchanges())
    markets: list[MarketListing] = []
    errors: list[str] = []
    counts: dict[str, int] = {}

    def load(name: str) -> tuple[str, list[MarketListing]]:
        return name, CLIENTS[name]().list_markets()

    # Exchanges are independent. Parallel loading keeps one slow or rate-limited
    # venue from delaying the complete discovery cycle.
    with ThreadPoolExecutor(max_workers=min(len(names), 7) or 1) as executor:
        futures = {executor.submit(load, name): name for name in names}
        for future in as_completed(futures):
            name = futures[future]
            try:
                _, rows = future.result()
                markets.extend(rows)
                counts[name] = len(rows)
            except Exception as exc:  # one exchange must never break the whole scan
                LOGGER.warning("Exchange collection failed: %s: %s", name, exc)
                errors.append(f"{name}: {exc}")
                counts[name] = 0

    return {"markets": markets, "counts": counts, "errors": errors}


def aggregate_markets(markets: Iterable[MarketListing]) -> list[dict[str, Any]]:
    grouped: dict[str, list[MarketListing]] = {}
    for market in markets:
        grouped.setdefault(market.base_asset.upper(), []).append(market)

    result = []
    for base, rows in grouped.items():
        rows.sort(key=lambda x: (x.quote_volume_24h, x.exchange == "binance"), reverse=True)
        primary = rows[0]
        exchanges = sorted({x.exchange for x in rows})
        pairs = {x.exchange: x.symbol for x in rows}
        earliest = min((x.listed_at_ms for x in rows if x.listed_at_ms), default=None)
        total_volume = sum(max(0.0, x.quote_volume_24h) for x in rows)
        weighted_change = (
            sum(x.price_change_24h * max(0.0, x.quote_volume_24h) for x in rows) / total_volume
            if total_volume else primary.price_change_24h
        )
        result.append({
            "symbol": f"{base}USDT",
            "baseAsset": base,
            "quoteAsset": "USDT",
            "contractType": "SPOT_MULTI_EXCHANGE",
            "status": "TRADING",
            "onboardTimestamp": earliest,
            "lastPrice": primary.last_price,
            "quoteVolume24h": total_volume,
            "priceChange24h": weighted_change,
            "primaryExchange": primary.exchange,
            "exchangeCount": len(exchanges),
            "exchanges": exchanges,
            "pairs": pairs,
            "marketType": "spot",
        })
    result.sort(key=lambda x: (x["exchangeCount"], x["quoteVolume24h"]), reverse=True)
    return result
