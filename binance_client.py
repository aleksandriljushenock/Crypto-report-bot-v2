import json
import time
import hashlib
from pathlib import Path

import requests


class BinanceFuturesClient:
    def __init__(self, base_url, futures_data_url, timeout=15, cache_ttl_seconds=300):
        self.base_url = base_url.rstrip("/")
        self.futures_data_url = futures_data_url.rstrip("/")
        self.timeout = timeout
        self.cache_ttl_seconds = cache_ttl_seconds
        self.session = requests.Session()
        self.cache_dir = Path("cache")
        self.cache_dir.mkdir(exist_ok=True)

    def _cache_key(self, url, params):
        raw = url + "|" + json.dumps(params or {}, sort_keys=True)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _cache_path(self, url, params):
        return self.cache_dir / f"{self._cache_key(url, params)}.json"

    def _read_cache(self, url, params):
        path = self._cache_path(url, params)

        if not path.exists():
            return None

        age = time.time() - path.stat().st_mtime

        if age > self.cache_ttl_seconds:
            return None

        try:
            with path.open("r", encoding="utf-8") as file:
                return json.load(file)
        except Exception:
            return None

    def _write_cache(self, url, params, data):
        path = self._cache_path(url, params)

        try:
            with path.open("w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False)
        except Exception:
            pass

    def _get(self, url, params=None, use_cache=True):
        if use_cache:
            cached = self._read_cache(url, params)
            if cached is not None:
                return cached

        for attempt in range(3):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)

                if response.status_code == 429:
                    time.sleep(2 + attempt * 2)
                    continue

                response.raise_for_status()

                try:
                    data = response.json()
                except ValueError:
                    text_preview = response.text[:300]
                    raise RuntimeError(
                        f"Non-JSON response from {url} {params}. "
                        f"Status={response.status_code}. Body={text_preview}"
                    )

                if use_cache:
                    self._write_cache(url, params, data)

                return data

            except Exception as exc:
                if attempt == 2:
                    raise RuntimeError(f"Request failed: {url} {params} | {exc}") from exc
                time.sleep(1 + attempt)

    def exchange_info(self):
        return self._get(f"{self.base_url}/fapi/v1/exchangeInfo")

    def ticker_24h_all(self):
        return self._get(f"{self.base_url}/fapi/v1/ticker/24hr", use_cache=False)

    def ticker_24h(self, symbol):
        return self._get(
            f"{self.base_url}/fapi/v1/ticker/24hr",
            {"symbol": symbol},
            use_cache=False,
        )

    def klines(self, symbol, interval, limit):
        return self._get(
            f"{self.base_url}/fapi/v1/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )

    def depth(self, symbol, limit=100):
        return self._get(
            f"{self.base_url}/fapi/v1/depth",
            {"symbol": symbol, "limit": limit},
            use_cache=False,
        )

    def open_interest(self, symbol):
        return self._get(
            f"{self.base_url}/fapi/v1/openInterest",
            {"symbol": symbol},
            use_cache=False,
        )

    def premium_index(self, symbol):
        return self._get(
            f"{self.base_url}/fapi/v1/premiumIndex",
            {"symbol": symbol},
            use_cache=False,
        )

    def global_long_short_ratio(self, symbol, period="1h", limit=24):
        return self._get(
            f"{self.futures_data_url}/futures/data/globalLongShortAccountRatio",
            {"symbol": symbol, "period": period, "limit": limit},
        )

    def taker_buy_sell_volume(self, symbol, period="1h", limit=24):
        return self._get(
            f"{self.futures_data_url}/futures/data/takerlongshortRatio",
            {"symbol": symbol, "period": period, "limit": limit},
        )
    def open_interest_history(self, symbol, period="1h", limit=24):
        return self._get(
            f"{self.futures_data_url}/futures/data/openInterestHist",
            {"symbol": symbol, "period": period, "limit": limit},
        )