"""Single provider registry for every futures venue used by the bot."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Any

from config import BASE_URL, FUTURES_DATA_URL
from core.runtime_config import DEFAULT_PROVIDERS, csv, string
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


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    base_env: str | None
    default_base: str | None
    factory: Callable[[int, str | None], Any]


def _binance(timeout: int, _base: str | None):
    return BinanceFuturesClient(BASE_URL, FUTURES_DATA_URL, timeout=timeout)


def _simple(cls):
    return lambda timeout, base: cls(base_url=base, timeout=timeout)


SPECS: dict[str, ProviderSpec] = {
    "binance": ProviderSpec("binance", None, None, _binance),
    "bybit": ProviderSpec("bybit", "BYBIT_API_BASE", "https://api.bybit.com", _simple(BybitFuturesClient)),
    "okx": ProviderSpec("okx", "OKX_API_BASE", "https://www.okx.com", _simple(OkxFuturesClient)),
    "bitget": ProviderSpec("bitget", "BITGET_API_BASE", "https://api.bitget.com", _simple(BitgetFuturesClient)),
    "gate": ProviderSpec("gate", "GATE_API_BASE", "https://api.gateio.ws/api/v4", _simple(GateFuturesClient)),
    "mexc": ProviderSpec("mexc", "MEXC_FUTURES_API_BASE", "https://contract.mexc.com", _simple(MexcFuturesClient)),
    "bingx": ProviderSpec("bingx", "BINGX_API_BASE", "https://open-api.bingx.com", _simple(BingxFuturesClient)),
    "kucoin": ProviderSpec("kucoin", "KUCOIN_FUTURES_API_BASE", "https://api-futures.kucoin.com", _simple(KucoinFuturesClient)),
    "hyperliquid": ProviderSpec("hyperliquid", "HYPERLIQUID_API_BASE", "https://api.hyperliquid.xyz", _simple(HyperliquidFuturesClient)),
    "htx": ProviderSpec("htx", "HTX_FUTURES_API_BASE", "https://api.hbdm.com", _simple(HtxFuturesClient)),
}


def configured_names() -> list[str]:
    names = csv("TRADE_MARKET_PROVIDERS", DEFAULT_PROVIDERS, strategy=False)
    valid = [name for name in names if name in SPECS]
    return valid or list(DEFAULT_PROVIDERS)


def create(name: str, timeout: int = 15):
    key = str(name).strip().lower()
    spec = SPECS.get(key)
    if not spec:
        raise ValueError(f"Unsupported trade market provider: {name}")
    base = string(spec.base_env, spec.default_base or "", strategy=False) if spec.base_env else None
    return spec.factory(timeout, base)


def supported_names() -> tuple[str, ...]:
    return tuple(SPECS)
