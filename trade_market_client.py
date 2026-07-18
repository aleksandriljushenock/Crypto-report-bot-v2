import os

from binance_client import BinanceFuturesClient
from bybit_futures_client import BybitFuturesClient
from config import BASE_URL, FUTURES_DATA_URL


def create_trade_market_client():
    provider = os.getenv("TRADE_MARKET_PROVIDER", "bybit").strip().lower()
    timeout = int(os.getenv("EXCHANGE_HTTP_TIMEOUT", "15"))
    if provider == "binance":
        return BinanceFuturesClient(BASE_URL, FUTURES_DATA_URL, timeout=timeout)
    if provider == "bybit":
        return BybitFuturesClient(
            base_url=os.getenv("BYBIT_API_BASE", "https://api.bybit.com"),
            timeout=timeout,
        )
    raise ValueError(f"Unsupported TRADE_MARKET_PROVIDER: {provider}")
