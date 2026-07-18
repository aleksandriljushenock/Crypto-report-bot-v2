"""Smart Money data-source adapters.

Public/free endpoints are used where possible. Components that normally require
paid on-chain providers have transparent market-proxy fallbacks, marked with
``quality='proxy'`` so consumers can distinguish them from direct measurements.
"""
from __future__ import annotations

import math
import os
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Callable

from core.http_client import http
from core.logging_setup import get_logger

logger = get_logger(__name__)

BINANCE_SPOT = os.getenv("BINANCE_SPOT_API", "https://api.binance.com")
BINANCE_FUTURES = os.getenv("BINANCE_FUTURES_API", "https://fapi.binance.com")
DEFILLAMA_STABLECOINS = os.getenv("DEFILLAMA_STABLECOINS_API", "https://stablecoins.llama.fi")
FARSIDE_BTC_URL = os.getenv("FARSIDE_BTC_ETF_URL", "https://farside.co.uk/bitcoin-etf-flow-all-data/")
BYBIT_API = os.getenv("BYBIT_API", "https://api.bybit.com")
OKX_API = os.getenv("OKX_API", "https://www.okx.com")


def _provider_order() -> list[str]:
    configured = os.getenv("SMART_MONEY_EXCHANGES", "").strip()
    if configured:
        return [item.strip().lower() for item in configured.split(",") if item.strip()]
    # Binance commonly blocks shared cloud IPs with HTTP 418. Prefer Bybit on Render.
    return ["bybit", "okx", "binance"] if os.getenv("RENDER") or os.getenv("RENDER_SERVICE_ID") else ["binance", "bybit", "okx"]


def _base_asset(symbol: str) -> str:
    value = symbol.upper().replace("-", "").replace("_", "")
    for quote in ("USDT", "USDC", "USD", "BTC", "ETH"):
        if value.endswith(quote) and len(value) > len(quote):
            return value[:-len(quote)]
    return value


def _okx_symbol(symbol: str, futures: bool = False) -> str:
    base = _base_asset(symbol)
    return f"{base}-USDT-SWAP" if futures else f"{base}-USDT"


def _first_success(action: str, providers: dict[str, Callable[[], Any]]) -> tuple[Any, str]:
    errors: list[str] = []
    for name in _provider_order():
        fn = providers.get(name)
        if fn is None:
            continue
        try:
            return fn(), name
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            logger.info("Smart Money %s provider %s unavailable: %s", action, name, exc)
    raise RuntimeError(f"No Smart Money provider succeeded for {action}: {'; '.join(errors)}")


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _score_signed(value: float, scale: float) -> float:
    """Map signed value to 0..100 with 50 neutral using a smooth tanh curve."""
    if not math.isfinite(value):
        return 50.0
    return _clamp(50.0 + 50.0 * math.tanh(value / max(scale, 1e-9)))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class SourceResult:
    component: str
    available: bool
    score: float | None = None
    value: float | None = None
    unit: str = ""
    source: str = ""
    quality: str = "direct"  # direct | proxy | optional
    note: str = ""
    error: str = ""
    observed_at: str = ""
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if not data["observed_at"]:
            data["observed_at"] = datetime.now(timezone.utc).isoformat()
        data["score"] = round(data["score"], 2) if data["score"] is not None else None
        data["value"] = round(data["value"], 6) if data["value"] is not None else None
        data["metadata"] = data["metadata"] or {}
        return data


def _bybit_funding(symbol: str) -> float:
    payload = http.get_json(
        f"{BYBIT_API}/v5/market/funding/history",
        params={"category": "linear", "symbol": symbol.upper(), "limit": 1},
        cache_ttl=60,
    )
    rows = ((payload or {}).get("result") or {}).get("list") or []
    if not rows:
        raise ValueError("Bybit returned no funding rows")
    return _float(rows[0].get("fundingRate"))


def _okx_funding(symbol: str) -> float:
    payload = http.get_json(
        f"{OKX_API}/api/v5/public/funding-rate",
        params={"instId": _okx_symbol(symbol, futures=True)},
        cache_ttl=60,
    )
    rows = (payload or {}).get("data") or []
    if not rows:
        raise ValueError("OKX returned no funding rows")
    return _float(rows[0].get("fundingRate"))


def collect_funding(symbol: str) -> SourceResult:
    def binance() -> float:
        data = http.get_json(f"{BINANCE_FUTURES}/fapi/v1/premiumIndex", params={"symbol": symbol})
        return _float(data.get("lastFundingRate"))

    rate, provider = _first_success("funding", {
        "binance": binance,
        "bybit": lambda: _bybit_funding(symbol),
        "okx": lambda: _okx_funding(symbol),
    })
    label = {"binance": "Binance Futures", "bybit": "Bybit Linear", "okx": "OKX Swap"}[provider]
    return SourceResult("funding", True, _score_signed(-rate, 0.0005), rate * 100, "%", f"{label} funding", "direct", "Negative funding is scored bullish; extreme positive funding is scored bearish.", metadata={"provider": provider})


def _bybit_open_interest(symbol: str) -> tuple[float, float]:
    payload = http.get_json(
        f"{BYBIT_API}/v5/market/open-interest",
        params={"category": "linear", "symbol": symbol.upper(), "intervalTime": "5min", "limit": 24},
        cache_ttl=90,
    )
    rows = ((payload or {}).get("result") or {}).get("list") or []
    if not rows:
        raise ValueError("Bybit returned no open-interest rows")
    values = [_float(row.get("openInterest")) for row in rows if _float(row.get("openInterest")) > 0]
    if not values:
        raise ValueError("Bybit open-interest rows were invalid")
    return values[0], values[-1]


def collect_open_interest(symbol: str) -> SourceResult:
    def binance() -> tuple[float, float]:
        current = http.get_json(f"{BINANCE_FUTURES}/fapi/v1/openInterest", params={"symbol": symbol})
        history = http.get_json(
            f"{BINANCE_FUTURES}/futures/data/openInterestHist",
            params={"symbol": symbol, "period": "5m", "limit": 24},
            cache_ttl=90,
        )
        now = _float(current.get("openInterest"))
        old = _float(history[0].get("sumOpenInterest")) if isinstance(history, list) and history else 0.0
        return now, old

    (now_oi, old_oi), provider = _first_success("open_interest", {
        "binance": binance,
        "bybit": lambda: _bybit_open_interest(symbol),
    })
    change = ((now_oi - old_oi) / old_oi * 100.0) if old_oi else 0.0
    label = "Binance Futures" if provider == "binance" else "Bybit Linear"
    return SourceResult("open_interest", True, _score_signed(change, 3.0), change, "% change/~2h", f"{label} open interest", "direct", "Rising OI is treated as increasing institutional participation; direction is refined by other components.", metadata={"open_interest": now_oi, "provider": provider})


def _normalize_trade(price: Any, qty: Any, side: str) -> dict[str, Any]:
    return {"p": str(price), "q": str(qty), "m": side.lower() == "sell"}


def _bybit_recent_trades(symbol: str, futures: bool, limit: int) -> list[dict[str, Any]]:
    payload = http.get_json(
        f"{BYBIT_API}/v5/market/recent-trade",
        params={"category": "linear" if futures else "spot", "symbol": symbol.upper(), "limit": min(limit, 1000)},
        cache_ttl=30,
    )
    rows = ((payload or {}).get("result") or {}).get("list") or []
    if not rows:
        raise ValueError("Bybit returned no recent trades")
    return [_normalize_trade(row.get("price"), row.get("size"), str(row.get("side", ""))) for row in rows]


def _okx_recent_trades(symbol: str, futures: bool, limit: int) -> list[dict[str, Any]]:
    payload = http.get_json(
        f"{OKX_API}/api/v5/market/trades",
        params={"instId": _okx_symbol(symbol, futures=futures), "limit": min(limit, 500)},
        cache_ttl=30,
    )
    rows = (payload or {}).get("data") or []
    if not rows:
        raise ValueError("OKX returned no recent trades")
    return [_normalize_trade(row.get("px"), row.get("sz"), str(row.get("side", ""))) for row in rows]


def _recent_agg_trades(symbol: str, futures: bool = False, limit: int = 500) -> tuple[list[dict[str, Any]], str]:
    def binance() -> list[dict[str, Any]]:
        base = BINANCE_FUTURES if futures else BINANCE_SPOT
        path = "/fapi/v1/aggTrades" if futures else "/api/v3/aggTrades"
        return http.get_json(f"{base}{path}", params={"symbol": symbol, "limit": limit}, cache_ttl=30)

    return _first_success(
        "futures_trades" if futures else "spot_trades",
        {
            "binance": binance,
            "bybit": lambda: _bybit_recent_trades(symbol, futures, limit),
            "okx": lambda: _okx_recent_trades(symbol, futures, limit),
        },
    )


def _trade_flow(trades: list[dict[str, Any]]) -> tuple[float, float, float, int]:
    buy = sell = total = 0.0
    large = 0
    notionals = []
    parsed = []
    for row in trades or []:
        qty, price = _float(row.get("q")), _float(row.get("p"))
        notion = qty * price
        if notion <= 0:
            continue
        parsed.append((notion, bool(row.get("m"))))
        notionals.append(notion)
        total += notion
    threshold = max(100_000.0, (sorted(notionals)[int(len(notionals) * 0.9)] if notionals else 0.0))
    for notion, buyer_maker in parsed:
        if buyer_maker:
            sell += notion
        else:
            buy += notion
        if notion >= threshold:
            large += 1
    imbalance = (buy - sell) / total * 100.0 if total else 0.0
    return imbalance, total, threshold, large


def collect_exchange_netflow(symbol: str) -> SourceResult:
    trades, provider = _recent_agg_trades(symbol, futures=False)
    imbalance, total, _, _ = _trade_flow(trades)
    return SourceResult("exchange_netflow", True, _score_signed(imbalance, 15.0), imbalance, "% taker imbalance", f"{provider.title()} Spot trades", "proxy", "Free fallback proxy: taker buy/sell imbalance, not wallet-labelled on-chain exchange netflow.", metadata={"sample_notional_usd": round(total, 2), "provider": provider})


def collect_whale_activity(symbol: str) -> SourceResult:
    trades, provider = _recent_agg_trades(symbol, futures=False, limit=1000)
    imbalance, total, threshold, large_count = _trade_flow(trades)
    large_rows = []
    for row in trades or []:
        notion = _float(row.get("q")) * _float(row.get("p"))
        if notion >= threshold:
            large_rows.append((notion, bool(row.get("m"))))
    large_total = sum(x[0] for x in large_rows)
    large_buy = sum(x[0] for x in large_rows if not x[1])
    large_sell = sum(x[0] for x in large_rows if x[1])
    large_imbalance = (large_buy - large_sell) / large_total * 100.0 if large_total else imbalance
    return SourceResult("whale_alert", True, _score_signed(large_imbalance, 20.0), large_imbalance, "% large-trade imbalance", f"{provider.title()} Spot trades", "proxy", "Free whale proxy based on unusually large aggressive trades; not labelled blockchain transfers.", metadata={"large_trade_threshold_usd": round(threshold, 2), "large_trade_count": large_count, "sample_notional_usd": round(total, 2), "provider": provider})


def collect_liquidations(symbol: str) -> SourceResult:
    api_key = os.getenv("COINGLASS_API_KEY", "").strip()
    if api_key:
        url = os.getenv("COINGLASS_LIQUIDATION_URL", "https://open-api-v4.coinglass.com/api/futures/liquidation/coin-list")
        response = http.get(url, headers={"CG-API-KEY": api_key}, params={"symbol": symbol.replace("USDT", "")})
        payload = response.json()
        rows = payload.get("data") or []
        row = rows[0] if isinstance(rows, list) and rows else payload.get("data", {})
        long_liq = _float(row.get("longLiquidationUsd") or row.get("longLiquidation"))
        short_liq = _float(row.get("shortLiquidationUsd") or row.get("shortLiquidation"))
        total = long_liq + short_liq
        imbalance = (short_liq - long_liq) / total * 100.0 if total else 0.0
        return SourceResult("liquidations", True, _score_signed(imbalance, 25.0), imbalance, "% short-vs-long", "CoinGlass", "direct", metadata={"long_liquidation_usd": long_liq, "short_liquidation_usd": short_liq})

    trades, provider = _recent_agg_trades(symbol, futures=True, limit=1000)
    imbalance, total, threshold, large_count = _trade_flow(trades)
    return SourceResult("liquidations", True, _score_signed(imbalance, 22.0), imbalance, "% large futures trade imbalance", f"{provider.title()} Futures trades", "proxy", "No liquidation API key configured; large aggressive futures trades are used as a stress/liquidation proxy.", metadata={"large_trade_threshold_usd": round(threshold, 2), "large_trade_count": large_count, "sample_notional_usd": round(total, 2), "provider": provider})


def collect_stablecoin_flow(_: str) -> SourceResult:
    series = http.get_json(f"{DEFILLAMA_STABLECOINS}/stablecoincharts/all", cache_ttl=900)
    points = series if isinstance(series, list) else series.get("data", [])
    values = []
    for point in points[-14:]:
        total = point.get("totalCirculatingUSD") or point.get("totalCirculating") or {}
        value = total.get("peggedUSD") if isinstance(total, dict) else total
        if value is not None:
            values.append(_float(value))
    if len(values) < 2 or values[0] <= 0:
        raise ValueError("Stablecoin history did not contain enough valid points")
    change = (values[-1] - values[0]) / values[0] * 100.0
    return SourceResult("stablecoin_flow", True, _score_signed(change, 1.5), change, "% supply change", "DefiLlama Stablecoins", "direct", "Growth in stablecoin supply is treated as potential deployable crypto liquidity.", metadata={"latest_supply_usd": values[-1], "period_points": len(values)})


def _parse_farside_total(html: str) -> float:
    # Farside tables use daily rows and a final Total column. Parse the newest
    # row containing a date and extract the last numeric cell.
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.I | re.S)
    for row in reversed(rows):
        clean = re.sub(r"<[^>]+>", " ", row)
        clean = re.sub(r"\s+", " ", clean).strip()
        if not re.search(r"\b\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\b|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", clean):
            continue
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.I | re.S)
        nums = []
        for cell in cells[1:]:
            text = re.sub(r"<[^>]+>", "", cell).replace(",", "").strip()
            text = text.replace("(", "-").replace(")", "")
            match = re.search(r"-?\d+(?:\.\d+)?", text)
            if match:
                nums.append(float(match.group()))
        if nums:
            return nums[-1]
    raise ValueError("Could not locate latest ETF total in Farside table")


def collect_etf_flow(symbol: str) -> SourceResult:
    if not symbol.upper().startswith("BTC"):
        return SourceResult("etf_flow", False, source="Farside Investors", quality="optional", note="BTC ETF flow is not applied to non-BTC symbols.")
    html = http.get(FARSIDE_BTC_URL, headers={"User-Agent": "Mozilla/5.0 crypto-report-service"}).text
    flow_musd = _parse_farside_total(html)
    return SourceResult("etf_flow", True, _score_signed(flow_musd, 250.0), flow_musd, "USD million/day", "Farside Investors", "direct", "Latest published US spot Bitcoin ETF daily net flow.")


COLLECTORS: dict[str, Callable[[str], SourceResult]] = {
    "whale_alert": collect_whale_activity,
    "exchange_netflow": collect_exchange_netflow,
    "etf_flow": collect_etf_flow,
    "stablecoin_flow": collect_stablecoin_flow,
    "funding": collect_funding,
    "open_interest": collect_open_interest,
    "liquidations": collect_liquidations,
}


def safe_collect(name: str, symbol: str) -> dict[str, Any]:
    started = time.monotonic()
    try:
        result = COLLECTORS[name](symbol)
    except Exception as exc:
        logger.warning("Smart Money source %s failed: %s", name, exc)
        result = SourceResult(name, False, source=name, error=f"{type(exc).__name__}: {exc}", note="Source failed; the engine continues with remaining components.")
    data = result.to_dict()
    data["latency_ms"] = round((time.monotonic() - started) * 1000)
    return data
