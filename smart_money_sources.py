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


def collect_funding(symbol: str) -> SourceResult:
    data = http.get_json(f"{BINANCE_FUTURES}/fapi/v1/premiumIndex", params={"symbol": symbol})
    rate = _float(data.get("lastFundingRate"))
    # Negative funding can indicate crowded shorts / accumulation opportunity.
    return SourceResult("funding", True, _score_signed(-rate, 0.0005), rate * 100, "%", "Binance Futures premiumIndex", "direct", "Negative funding is scored bullish; extreme positive funding is scored bearish.")


def collect_open_interest(symbol: str) -> SourceResult:
    current = http.get_json(f"{BINANCE_FUTURES}/fapi/v1/openInterest", params={"symbol": symbol})
    history = http.get_json(
        f"{BINANCE_FUTURES}/futures/data/openInterestHist",
        params={"symbol": symbol, "period": "5m", "limit": 24}, cache_ttl=90,
    )
    now_oi = _float(current.get("openInterest"))
    old_oi = _float(history[0].get("sumOpenInterest")) if isinstance(history, list) and history else 0.0
    change = ((now_oi - old_oi) / old_oi * 100.0) if old_oi else 0.0
    return SourceResult("open_interest", True, _score_signed(change, 3.0), change, "% change/2h", "Binance Futures openInterest", "direct", "Rising OI is treated as increasing institutional participation; direction is refined by other components.", metadata={"open_interest": now_oi})


def _recent_agg_trades(symbol: str, futures: bool = False, limit: int = 500) -> list[dict[str, Any]]:
    base = BINANCE_FUTURES if futures else BINANCE_SPOT
    return http.get_json(f"{base}/api/v3/aggTrades" if not futures else f"{base}/fapi/v1/aggTrades", params={"symbol": symbol, "limit": limit}, cache_ttl=30)


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
        parsed.append((notion, bool(row.get("m"))))  # m=True: buyer was maker => aggressive sell
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
    trades = _recent_agg_trades(symbol, futures=False)
    imbalance, total, _, _ = _trade_flow(trades)
    return SourceResult("exchange_netflow", True, _score_signed(imbalance, 15.0), imbalance, "% taker imbalance", "Binance Spot aggTrades", "proxy", "Free fallback proxy: taker buy/sell imbalance, not wallet-labelled on-chain exchange netflow.", metadata={"sample_notional_usd": round(total, 2)})


def collect_whale_activity(symbol: str) -> SourceResult:
    trades = _recent_agg_trades(symbol, futures=False, limit=1000)
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
    return SourceResult("whale_alert", True, _score_signed(large_imbalance, 20.0), large_imbalance, "% large-trade imbalance", "Binance Spot aggTrades", "proxy", "Free whale proxy based on unusually large aggressive trades; not labelled blockchain transfers.", metadata={"large_trade_threshold_usd": round(threshold, 2), "large_trade_count": large_count, "sample_notional_usd": round(total, 2)})


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

    trades = _recent_agg_trades(symbol, futures=True, limit=1000)
    imbalance, total, threshold, large_count = _trade_flow(trades)
    return SourceResult("liquidations", True, _score_signed(imbalance, 22.0), imbalance, "% large futures trade imbalance", "Binance Futures aggTrades", "proxy", "No liquidation API key configured; large aggressive futures trades are used as a stress/liquidation proxy.", metadata={"large_trade_threshold_usd": round(threshold, 2), "large_trade_count": large_count, "sample_notional_usd": round(total, 2)})


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
