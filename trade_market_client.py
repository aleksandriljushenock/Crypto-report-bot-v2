import logging
import threading
import time

from exchanges.registry import configured_names, create as create_provider, supported_names
from exchanges.capabilities import CapabilityValue
from core.runtime_config import integer, number, string
from market_errors import UnsupportedSymbolError

logger = logging.getLogger("trade_market_client")

_PROVIDER_HEALTH = {}
_PROVIDER_SYMBOLS = {}
_UNSUPPORTED_SYMBOLS = {}
_PROVIDER_MARKET_COUNTS = {}
_LAST_UNIVERSE_SUMMARY = {}
_PROVIDER_LOCK = threading.Lock()
_RATE_LOCK = threading.Lock()
_PROVIDER_LAST_CALL = {}
_METHOD_HEALTH = {}


def _provider_order():
    return configured_names()


def _cooldown_seconds():
    return integer("EXCHANGE_PROVIDER_COOLDOWN_SECONDS", 900, minimum=60, strategy=False)


def _mark_failed(provider, exc, method: str | None = None):
    now = time.time()
    target = _METHOD_HEALTH if method else _PROVIDER_HEALTH
    key = (provider, method) if method else provider
    with _PROVIDER_LOCK:
        state = dict(target.get(key) or {})
        state.update({"blocked_until": now + _cooldown_seconds(), "error": f"{type(exc).__name__}: {exc}",
                      "last_failure_at": now, "failures": int(state.get("failures", 0)) + 1})
        target[key] = state


def _mark_soft_failure(provider, exc, method: str | None = None):
    now = time.time(); target = _METHOD_HEALTH if method else _PROVIDER_HEALTH; key=(provider,method) if method else provider
    with _PROVIDER_LOCK:
        state=dict(target.get(key) or {}); state.update({"blocked_until":0,"error":f"{type(exc).__name__}: {exc}",
            "last_failure_at":now,"failures":int(state.get("failures",0))+1}); target[key]=state


def _should_trip_provider(exc):
    text = f"{type(exc).__name__}: {exc}".lower()
    markers = ("timeout","timed out","connectionerror","connection error","name resolution","429","418","500","502","503","504","too many requests","rate limit","temporarily unavailable")
    return any(marker in text for marker in markers)


def _mark_success(provider, method: str | None = None):
    now=time.time()
    with _PROVIDER_LOCK:
        state=dict(_PROVIDER_HEALTH.get(provider) or {}); state.update({"error":None,"last_success_at":now,"successes":int(state.get("successes",0))+1}); _PROVIDER_HEALTH[provider]=state
        if method:
            _METHOD_HEALTH.pop((provider,method), None)


def _available(provider, method: str | None = None):
    now=time.time()
    with _PROVIDER_LOCK:
        state=_PROVIDER_HEALTH.get(provider)
        if state and float(state.get("blocked_until",0)) > now:
            return False
        if method:
            mstate=_METHOD_HEALTH.get((provider,method))
            if mstate and float(mstate.get("blocked_until",0)) > now:
                return False
        return True


def _method_weight(method: str) -> float:
    weights = {
        "exchange_info": 2.0, "ticker_24h_all": 4.0, "ticker_24h": 1.0,
        "klines": 2.0, "depth": 5.0, "open_interest_history": 2.0,
        "global_long_short_ratio": 2.0, "taker_buy_sell_volume": 2.0,
    }
    return max(1.0, float(weights.get(str(method or ""), 1.0)))

def _rate_limit(provider, method: str = ""):
    """Weighted process-wide pacing with an instance-count budget divisor."""
    rps = max(0.2, number("EXCHANGE_PROVIDER_MAX_RPS", 8.0, minimum=0.2, maximum=50.0, strategy=False))
    instances = max(1, integer("EXCHANGE_EXPECTED_INSTANCE_COUNT", 1, minimum=1, maximum=100, strategy=False))
    effective_rps = max(0.1, rps / instances)
    gap = _method_weight(method) / effective_rps
    while True:
        with _RATE_LOCK:
            now = time.monotonic(); last = float(_PROVIDER_LAST_CALL.get(provider, 0.0)); wait = gap - (now - last)
            if wait <= 0:
                _PROVIDER_LAST_CALL[provider] = now
                return
        time.sleep(min(wait, gap))

def _build_provider(name, timeout):
    return create_provider(name, timeout)

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
        if not _available(name):
            provider_stats[name] = {"ok": False, "skipped": "cooldown", "tradable": 0, "eligible": 0}
            continue
        try:
            client = _build_provider(name, timeout)
            _rate_limit(name, "exchange_info")
            info = client.exchange_info() or {}
            tradable = {
                str(row.get("symbol")).upper()
                for row in info.get("symbols", [])
                if row.get("status") == "TRADING" and row.get("quoteAsset") == "USDT" and row.get("contractType") == "PERPETUAL"
            }
            _register_provider_symbols(name, tradable)
            _rate_limit(name, "ticker_24h_all")
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

    min_venues = integer("MULTI_EXCHANGE_MIN_VENUES", 1, minimum=1, maximum=len(supported_names()))
    coverage_bonus = number("MULTI_EXCHANGE_COVERAGE_BONUS", 0.08, minimum=0.0, maximum=2.0)
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
    wide_limit = max(limit, integer("FAST_SCAN_POOL_SIZE", 500, minimum=1, maximum=2000))
    liquidity_rows = sorted(rows, key=lambda x: (x["liquidityRankScore"], x["exchangeCount"]), reverse=True)[:wide_limit]

    # Dynamic universe buckets. Defaults target ~80 deep symbols while keeping
    # the same total limit configured by TRADE_TOP_LIQUID_SYMBOLS.
    liq_n = min(limit, max(1, integer("DYNAMIC_UNIVERSE_LIQUID_COUNT", max(1, round(limit * 0.58)), minimum=1)))
    gain_n = max(0, integer("DYNAMIC_UNIVERSE_GAINERS_COUNT", max(4, round(limit * 0.14)), minimum=0))
    loss_n = max(0, integer("DYNAMIC_UNIVERSE_LOSERS_COUNT", max(4, round(limit * 0.14)), minimum=0))
    cover_n = max(0, integer("DYNAMIC_UNIVERSE_COVERAGE_COUNT", max(4, round(limit * 0.14)), minimum=0))

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
            "activeMarketSymbols": sum(1 for x in liquidity_rows if abs(float(x.get("crossExchangeChangeMedian") or 0)) >= number("ACTIVE_MARKET_CHANGE_PCT", 5.0, minimum=0.0)),
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
            if not _available(name, method):
                continue
            if symbol and not _provider_supports_symbol(name, symbol):
                continue
            attempted.add(name)
            try:
                _rate_limit(name, method)
                value = getattr(self.clients[name], method)(*args, **kwargs)
                _mark_success(name, method)
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
                    _mark_failed(name, exc, method)
                    logger.warning("Trade market provider failed: method=%s provider=%s error=%s", method, name, exc)
                else:
                    _mark_soft_failure(name, exc, method)
                    logger.debug("Trade market method unavailable: method=%s provider=%s error=%s", method, name, exc)
                errors.append(f"{name}: {type(exc).__name__}: {exc}")

        if not attempted:
            errors.append("all eligible providers are on cooldown or unsupported")

        self.last_errors = errors
        raise RuntimeError(f"No trade market provider succeeded for {method}: {'; '.join(errors)}")

    def capability(self, method, *args, **kwargs):
        """Return explicit availability instead of coercing missing data to zero."""
        try:
            value = self._call(method, *args, **kwargs)
            return CapabilityValue("supported", value=value, provider=self.last_provider)
        except RuntimeError as exc:
            text = str(exc).lower()
            status = "unsupported" if "unsupported" in text else "unavailable"
            return CapabilityValue(status, value=None, provider=None, error=str(exc))

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

def create_trade_market_client(providers=None):
    timeout = integer("EXCHANGE_HTTP_TIMEOUT", 15, minimum=1, maximum=60, strategy=False)
    if providers is None:
        legacy = string("TRADE_MARKET_PROVIDER", "", strategy=False).lower()
        providers = [legacy] if legacy in set(supported_names()) else _provider_order()
    providers = [p for p in (providers or []) if p in set(supported_names())]
    return FallbackTradeMarketClient(providers=providers, timeout=timeout)
