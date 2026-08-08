import logging
import os
import threading
import time

from binance_client import BinanceFuturesClient
from bybit_futures_client import BybitFuturesClient
from okx_futures_client import OkxFuturesClient
from bitget_futures_client import BitgetFuturesClient
from gate_futures_client import GateFuturesClient
from mexc_futures_client import MexcFuturesClient
from bingx_futures_client import BingxFuturesClient
from kucoin_futures_client import KucoinFuturesClient
from hyperliquid_futures_client import HyperliquidFuturesClient
from htx_futures_client import HtxFuturesClient
from market_errors import UnsupportedSymbolError
from config import BASE_URL, FUTURES_DATA_URL

logger = logging.getLogger("trade_market_client")

_PROVIDER_HEALTH = {}
_PROVIDER_SYMBOLS = {}
_UNSUPPORTED_SYMBOLS = {}
_PROVIDER_MARKET_COUNTS = {}
_LAST_UNIVERSE_SUMMARY = {}
_PROVIDER_LOCK = threading.Lock()


def _provider_order():
    raw = os.getenv("TRADE_MARKET_PROVIDERS", "binance,bybit,okx,bitget,gate,mexc,bingx,kucoin,hyperliquid,htx")
    order = [item.strip().lower() for item in raw.split(",") if item.strip()]
    return order or ["binance", "bybit", "okx", "bitget", "gate", "mexc", "bingx", "kucoin", "hyperliquid", "htx"]


def _cooldown_seconds():
    return max(60, int(os.getenv("EXCHANGE_PROVIDER_COOLDOWN_SECONDS", "900")))


def _mark_failed(provider, exc):
    now = time.time()
    with _PROVIDER_LOCK:
        state = dict(_PROVIDER_HEALTH.get(provider) or {})
        state.update({
            "blocked_until": now + _cooldown_seconds(),
            "error": f"{type(exc).__name__}: {exc}",
            "last_failure_at": now,
            "failures": int(state.get("failures", 0)) + 1,
        })
        _PROVIDER_HEALTH[provider] = state


def _mark_soft_failure(provider, exc):
    now = time.time()
    with _PROVIDER_LOCK:
        state = dict(_PROVIDER_HEALTH.get(provider) or {})
        state.update({
            "blocked_until": 0,
            "error": f"{type(exc).__name__}: {exc}",
            "last_failure_at": now,
            "failures": int(state.get("failures", 0)) + 1,
        })
        _PROVIDER_HEALTH[provider] = state


def _should_trip_provider(exc):
    text = f"{type(exc).__name__}: {exc}".lower()
    markers = (
        "timeout", "timed out", "connectionerror", "connection error",
        "name resolution", "429", "418", "500", "502", "503", "504",
        "too many requests", "rate limit", "temporarily unavailable",
    )
    return any(marker in text for marker in markers)


def _mark_success(provider):
    now = time.time()
    with _PROVIDER_LOCK:
        state = dict(_PROVIDER_HEALTH.get(provider) or {})
        state.update({
            "blocked_until": 0,
            "error": None,
            "last_success_at": now,
            "successes": int(state.get("successes", 0)) + 1,
        })
        _PROVIDER_HEALTH[provider] = state


def _available(provider):
    with _PROVIDER_LOCK:
        state = _PROVIDER_HEALTH.get(provider)
        return not state or float(state.get("blocked_until", 0)) <= time.time()


def _build_provider(name, timeout):
    if name == "binance":
        return BinanceFuturesClient(BASE_URL, FUTURES_DATA_URL, timeout=timeout)
    if name == "bybit":
        return BybitFuturesClient(
            base_url=os.getenv("BYBIT_API_BASE", "https://api.bybit.com"),
            timeout=timeout,
        )
    if name == "okx":
        return OkxFuturesClient(base_url=os.getenv("OKX_API_BASE", "https://www.okx.com"), timeout=timeout)
    if name == "bitget":
        return BitgetFuturesClient(base_url=os.getenv("BITGET_API_BASE", "https://api.bitget.com"), timeout=timeout)
    if name == "gate":
        return GateFuturesClient(base_url=os.getenv("GATE_API_BASE", "https://api.gateio.ws/api/v4"), timeout=timeout)
    if name == "mexc":
        return MexcFuturesClient(base_url=os.getenv("MEXC_FUTURES_API_BASE", "https://contract.mexc.com"), timeout=timeout)
    if name == "bingx":
        return BingxFuturesClient(base_url=os.getenv("BINGX_API_BASE", "https://open-api.bingx.com"), timeout=timeout)
    if name == "kucoin":
        return KucoinFuturesClient(base_url=os.getenv("KUCOIN_FUTURES_API_BASE", "https://api-futures.kucoin.com"), timeout=timeout)
    if name == "hyperliquid":
        return HyperliquidFuturesClient(base_url=os.getenv("HYPERLIQUID_API_BASE", "https://api.hyperliquid.xyz"), timeout=timeout)
    if name == "htx":
        return HtxFuturesClient(base_url=os.getenv("HTX_FUTURES_API_BASE", "https://api.hbdm.com"), timeout=timeout)
    raise ValueError(f"Unsupported trade market provider: {name}")


def _mark_symbol_unsupported(provider, symbol):
    if not symbol:
        return
    with _PROVIDER_LOCK:
        _UNSUPPORTED_SYMBOLS.setdefault(provider, set()).add(str(symbol).upper())


def _register_provider_symbols(provider, symbols):
    normalized = {str(s).upper() for s in symbols if s}
    with _PROVIDER_LOCK:
        _PROVIDER_SYMBOLS[provider] = normalized


def _provider_supports_symbol(provider, symbol):
    if not symbol:
        return True
    symbol = str(symbol).upper()
    with _PROVIDER_LOCK:
        known = _PROVIDER_SYMBOLS.get(provider)
        unsupported = _UNSUPPORTED_SYMBOLS.get(provider, set())
    if symbol in unsupported:
        return False
    return not known or symbol in known


def collect_multi_exchange_universe(top_limit=30, min_quote_volume=0.0, timeout=8):
    """Build a deduplicated, diverse USDT perpetual universe across venues.

    v22 keeps the API-cheap ticker sweep wide, then selects the deep-scan set from
    multiple buckets instead of pure quote-volume rank: liquidity, gainers,
    losers, absolute movers and cross-exchange coverage. This increases useful
    opportunity coverage without loading candles for hundreds of symbols.
    """
    merged = {}
    provider_stats = {}
    for name in _provider_order():
        try:
            client = _build_provider(name, timeout)
            info = client.exchange_info() or {}
            tradable = {
                str(row.get("symbol")).upper()
                for row in info.get("symbols", [])
                if row.get("status") == "TRADING" and row.get("quoteAsset") == "USDT" and row.get("contractType") == "PERPETUAL"
            }
            _register_provider_symbols(name, tradable)
            tickers = client.ticker_24h_all() or []
            accepted = 0
            for row in tickers:
                symbol = str(row.get("symbol") or "").upper()
                if not symbol or symbol not in tradable:
                    continue
                try:
                    quote_volume = float(row.get("quoteVolume") or 0)
                except Exception:
                    quote_volume = 0.0
                if quote_volume < float(min_quote_volume or 0):
                    continue
                accepted += 1
                item = merged.setdefault(symbol, {
                    "symbol": symbol, "lastPrice": 0.0, "priceChangePercent": 0.0,
                    "quoteVolume": 0.0, "highPrice": 0.0, "lowPrice": 0.0,
                    "exchanges": [], "exchangeVolumes": {}, "exchangeChanges": {},
                })
                if name not in item["exchanges"]:
                    item["exchanges"].append(name)
                item["exchangeVolumes"][name] = quote_volume
                try:
                    item["exchangeChanges"][name] = float(row.get("priceChangePercent") or 0)
                except Exception:
                    item["exchangeChanges"][name] = 0.0
                if quote_volume >= float(item.get("quoteVolume") or 0):
                    item["quoteVolume"] = quote_volume
                    for key in ("lastPrice", "priceChangePercent", "highPrice", "lowPrice"):
                        try:
                            item[key] = float(row.get(key) or 0)
                        except Exception:
                            pass
            _mark_success(name)
            provider_stats[name] = {"ok": True, "tradable": len(tradable), "eligible": accepted}
            with _PROVIDER_LOCK:
                _PROVIDER_MARKET_COUNTS[name] = {"tradable": len(tradable), "eligible": accepted}
        except Exception as exc:
            _mark_failed(name, exc)
            provider_stats[name] = {"ok": False, "tradable": 0, "eligible": 0, "error": str(exc)}
            logger.warning("Universe provider failed: provider=%s error=%s", name, exc)

    min_venues = max(1, int(os.getenv("MULTI_EXCHANGE_MIN_VENUES", "1")))
    coverage_bonus = max(0.0, float(os.getenv("MULTI_EXCHANGE_COVERAGE_BONUS", "0.08")))
    rows = []
    for item in merged.values():
        item["exchangeCount"] = len(item["exchanges"])
        if item["exchangeCount"] < min_venues:
            continue
        changes = list(item.get("exchangeChanges", {}).values())
        if changes:
            changes_sorted = sorted(changes)
            item["crossExchangeChangeMedian"] = changes_sorted[len(changes_sorted)//2]
        else:
            item["crossExchangeChangeMedian"] = float(item.get("priceChangePercent") or 0)
        item["liquidityRankScore"] = float(item["quoteVolume"]) * (1.0 + coverage_bonus * max(0, item["exchangeCount"] - 1))
        rows.append(item)

    limit = max(1, int(top_limit))
    wide_limit = max(limit, int(os.getenv("FAST_SCAN_POOL_SIZE", "250")))
    liquidity_rows = sorted(rows, key=lambda x: (x["liquidityRankScore"], x["exchangeCount"]), reverse=True)[:wide_limit]

    # Dynamic universe buckets. Defaults target ~80 deep symbols while keeping
    # the same total limit configured by TRADE_TOP_LIQUID_SYMBOLS.
    liq_n = min(limit, max(1, int(os.getenv("DYNAMIC_UNIVERSE_LIQUID_COUNT", str(max(1, round(limit * 0.58)))))))
    gain_n = max(0, int(os.getenv("DYNAMIC_UNIVERSE_GAINERS_COUNT", str(max(4, round(limit * 0.14))))))
    loss_n = max(0, int(os.getenv("DYNAMIC_UNIVERSE_LOSERS_COUNT", str(max(4, round(limit * 0.14))))))
    cover_n = max(0, int(os.getenv("DYNAMIC_UNIVERSE_COVERAGE_COUNT", str(max(4, round(limit * 0.14))))))

    picked = {}
    def add_bucket(source, count, tag):
        for item in source:
            if len(picked) >= limit or count <= 0:
                break
            sym = item["symbol"]
            if sym not in picked:
                clone = dict(item)
                clone["fastScanBucket"] = tag
                picked[sym] = clone
                count -= 1

    add_bucket(sorted(liquidity_rows, key=lambda x: x["liquidityRankScore"], reverse=True), liq_n, "liquidity")
    add_bucket(sorted(liquidity_rows, key=lambda x: float(x.get("crossExchangeChangeMedian") or 0), reverse=True), gain_n, "gainer")
    add_bucket(sorted(liquidity_rows, key=lambda x: float(x.get("crossExchangeChangeMedian") or 0)), loss_n, "loser")
    add_bucket(sorted(liquidity_rows, key=lambda x: (int(x.get("exchangeCount") or 0), x["liquidityRankScore"]), reverse=True), cover_n, "coverage")
    # Fill any remaining slots with high absolute movers, then liquidity.
    add_bucket(sorted(liquidity_rows, key=lambda x: abs(float(x.get("crossExchangeChangeMedian") or 0)), reverse=True), limit, "mover")
    add_bucket(liquidity_rows, limit, "liquidity_fill")

    selected_rows = list(picked.values())[:limit]
    bucket_counts = {}
    for row in selected_rows:
        bucket_counts[row.get("fastScanBucket") or "other"] = bucket_counts.get(row.get("fastScanBucket") or "other", 0) + 1
    with _PROVIDER_LOCK:
        global _LAST_UNIVERSE_SUMMARY
        _LAST_UNIVERSE_SUMMARY = {
            "providersConfigured": len(_provider_order()),
            "providersOk": sum(1 for v in provider_stats.values() if v.get("ok")),
            "contractsObserved": sum(int(v.get("tradable") or 0) for v in provider_stats.values()),
            "uniqueLiquidSymbols": len(merged),
            "coverageEligibleSymbols": len(rows),
            "fastPoolSymbols": len(liquidity_rows),
            "selectedSymbols": len(selected_rows),
            "selectionBuckets": bucket_counts,
            "activeMarketSymbols": sum(1 for x in liquidity_rows if abs(float(x.get("crossExchangeChangeMedian") or 0)) >= float(os.getenv("ACTIVE_MARKET_CHANGE_PCT", "5"))),
            "maxAbsChangePct": max([abs(float(x.get("crossExchangeChangeMedian") or 0)) for x in liquidity_rows] or [0.0]),
            "minVenues": min_venues,
            "minQuoteVolumeUsdt": float(min_quote_volume or 0),
        }
    return selected_rows, provider_stats



def get_last_universe_summary():
    with _PROVIDER_LOCK:
        return dict(_LAST_UNIVERSE_SUMMARY)


class FallbackTradeMarketClient:
    """Binance-first futures client with circuit-breaker fallback.

    All public methods are delegated to providers in TRADE_MARKET_PROVIDERS.
    A provider that returns 418/429/5xx, times out, or raises another request
    error is temporarily skipped, preventing one blocked exchange from stopping
    scans and background workers.
    """

    def __init__(self, providers=None, timeout=15):
        self.provider_names = providers or _provider_order()
        self.clients = {name: _build_provider(name, timeout) for name in self.provider_names}
        self.last_provider = None
        self.last_errors = []

    def _call(self, method, *args, **kwargs):
        errors = []
        attempted = set()
        symbol = kwargs.get("symbol")
        if symbol is None and args and method not in {"ticker_24h_all", "exchange_info"}:
            first = args[0]
            if isinstance(first, str) and first.upper().endswith("USDT"):
                symbol = first.upper()

        for name in self.provider_names:
            if not _available(name):
                continue
            if symbol and not _provider_supports_symbol(name, symbol):
                continue
            attempted.add(name)
            try:
                value = getattr(self.clients[name], method)(*args, **kwargs)
                _mark_success(name)
                self.last_provider = name
                self.last_errors = errors
                if name != self.provider_names[0]:
                    logger.info("Trade market fallback: method=%s provider=%s", method, name)
                return value
            except UnsupportedSymbolError as exc:
                _mark_symbol_unsupported(name, symbol)
                errors.append(f"{name}: unsupported: {exc}")
                logger.debug("Trade market symbol unsupported: method=%s provider=%s symbol=%s", method, name, symbol)
            except Exception as exc:
                if _should_trip_provider(exc):
                    _mark_failed(name, exc)
                    logger.warning("Trade market provider failed: method=%s provider=%s error=%s", method, name, exc)
                else:
                    _mark_soft_failure(name, exc)
                    logger.debug("Trade market method unavailable: method=%s provider=%s error=%s", method, name, exc)
                errors.append(f"{name}: {type(exc).__name__}: {exc}")

        if not attempted:
            for name in self.provider_names:
                if symbol and not _provider_supports_symbol(name, symbol):
                    continue
                try:
                    value = getattr(self.clients[name], method)(*args, **kwargs)
                    _mark_success(name)
                    self.last_provider = name
                    self.last_errors = errors
                    return value
                except UnsupportedSymbolError as exc:
                    _mark_symbol_unsupported(name, symbol)
                    errors.append(f"{name}: unsupported: {exc}")
                except Exception as exc:
                    if _should_trip_provider(exc):
                        _mark_failed(name, exc)
                    else:
                        _mark_soft_failure(name, exc)
                    errors.append(f"{name}: {type(exc).__name__}: {exc}")

        self.last_errors = errors
        raise RuntimeError(f"No trade market provider succeeded for {method}: {'; '.join(errors)}")

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *args, **kwargs: self._call(name, *args, **kwargs)



def get_provider_health_snapshot():
    """Return local circuit-breaker/provider activity state without network I/O."""
    now = time.time()
    rows = []
    with _PROVIDER_LOCK:
        health = {k: dict(v) for k, v in _PROVIDER_HEALTH.items()}
    for name in _provider_order():
        state = health.get(name, {})
        blocked_until = float(state.get("blocked_until", 0) or 0)
        last_success = float(state.get("last_success_at", 0) or 0)
        last_failure = float(state.get("last_failure_at", 0) or 0)
        if blocked_until > now:
            status = "cooldown"
        elif last_success and last_success >= last_failure:
            status = "online"
        elif last_failure:
            status = "degraded"
        else:
            status = "unknown"
        rows.append({
            "provider": name,
            "status": status,
            "blocked_until": blocked_until,
            "cooldown_remaining": max(0, int(blocked_until - now)),
            "last_success_at": last_success,
            "last_failure_at": last_failure,
            "successes": int(state.get("successes", 0) or 0),
            "failures": int(state.get("failures", 0) or 0),
            "error": state.get("error"),
            "tradable_symbols": int((_PROVIDER_MARKET_COUNTS.get(name) or {}).get("tradable", 0)),
            "eligible_symbols": int((_PROVIDER_MARKET_COUNTS.get(name) or {}).get("eligible", 0)),
        })
    return rows


def probe_provider_health(symbol="BTCUSDT", timeout=5):
    """Actively ping configured exchanges with a lightweight ticker request."""
    results = []
    for name in _provider_order():
        started = time.perf_counter()
        try:
            client = _build_provider(name, timeout)
            client.ticker_24h(symbol)
            latency_ms = int((time.perf_counter() - started) * 1000)
            _mark_success(name)
            results.append({"provider": name, "ok": True, "latency_ms": latency_ms, "error": None})
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            _mark_failed(name, exc)
            results.append({"provider": name, "ok": False, "latency_ms": latency_ms, "error": str(exc)})
    return results

def create_trade_market_client():
    timeout = int(os.getenv("EXCHANGE_HTTP_TIMEOUT", "15"))
    legacy = os.getenv("TRADE_MARKET_PROVIDER", "").strip().lower()
    providers = [legacy] if legacy in {"binance", "bybit", "okx", "bitget", "gate", "mexc", "bingx", "kucoin", "hyperliquid", "htx"} else _provider_order()
    return FallbackTradeMarketClient(providers=providers, timeout=timeout)
