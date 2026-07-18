import logging
import os
import threading
import time

from binance_client import BinanceFuturesClient
from bybit_futures_client import BybitFuturesClient
from config import BASE_URL, FUTURES_DATA_URL

logger = logging.getLogger("trade_market_client")

_PROVIDER_HEALTH = {}
_PROVIDER_LOCK = threading.Lock()


def _provider_order():
    raw = os.getenv("TRADE_MARKET_PROVIDERS", "binance,bybit")
    order = [item.strip().lower() for item in raw.split(",") if item.strip()]
    return order or ["binance", "bybit"]


def _cooldown_seconds():
    return max(60, int(os.getenv("EXCHANGE_PROVIDER_COOLDOWN_SECONDS", "900")))


def _mark_failed(provider, exc):
    with _PROVIDER_LOCK:
        _PROVIDER_HEALTH[provider] = {
            "blocked_until": time.time() + _cooldown_seconds(),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _mark_success(provider):
    with _PROVIDER_LOCK:
        _PROVIDER_HEALTH.pop(provider, None)


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
    raise ValueError(f"Unsupported trade market provider: {name}")


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
        # First pass respects the circuit breaker.
        for name in self.provider_names:
            if not _available(name):
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
            except Exception as exc:
                _mark_failed(name, exc)
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
                logger.warning("Trade market provider failed: method=%s provider=%s error=%s", method, name, exc)

        # If every provider is cooling down, make one recovery attempt in order.
        if not attempted:
            for name in self.provider_names:
                try:
                    value = getattr(self.clients[name], method)(*args, **kwargs)
                    _mark_success(name)
                    self.last_provider = name
                    self.last_errors = errors
                    return value
                except Exception as exc:
                    _mark_failed(name, exc)
                    errors.append(f"{name}: {type(exc).__name__}: {exc}")

        self.last_errors = errors
        raise RuntimeError(f"No trade market provider succeeded for {method}: {'; '.join(errors)}")

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *args, **kwargs: self._call(name, *args, **kwargs)


def create_trade_market_client():
    timeout = int(os.getenv("EXCHANGE_HTTP_TIMEOUT", "15"))
    legacy = os.getenv("TRADE_MARKET_PROVIDER", "").strip().lower()
    providers = [legacy] if legacy in {"binance", "bybit"} else _provider_order()
    return FallbackTradeMarketClient(providers=providers, timeout=timeout)
