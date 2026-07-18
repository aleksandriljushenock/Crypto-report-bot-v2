from core.settings import env_int

BASE_URL = "https://fapi.binance.com"
FUTURES_DATA_URL = "https://futures.binance.com"
MIN_QUOTE_VOLUME_USDT = env_int("MIN_QUOTE_VOLUME_USDT", 100_000_000, 0)
TOP_LIMIT = env_int("TOP_LIMIT", 30, 1)
INTERVALS = {"5m": 240, "15m": 200, "1h": 200, "4h": 200, "1d": 120}
ORDERBOOK_LIMIT = env_int("ORDERBOOK_LIMIT", 100, 5)
LONG_SHORT_PERIOD = "1h"
TAKER_VOLUME_PERIOD = "1h"
