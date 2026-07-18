
import json
import time
import hashlib
from pathlib import Path

import requests


class MexcSpotClient:
    def __init__(
        self,
        base_url="https://api.mexc.com",
        timeout=15,
        cache_ttl_seconds=300,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.cache_ttl_seconds = cache_ttl_seconds

        self.session = requests.Session()
        

        self.cache_dir = Path("cache") / "mexc"
        self.cache_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

    def _cache_key(self, url, params):
        raw = (
            url
            + "|"
            + json.dumps(
                params or {},
                sort_keys=True,
            )
        )

        return hashlib.md5(
            raw.encode("utf-8")
        ).hexdigest()

    def _cache_path(self, url, params):
        return (
            self.cache_dir
            / f"{self._cache_key(url, params)}.json"
        )

    def _read_cache(self, url, params):
        path = self._cache_path(
            url,
            params,
        )

        if not path.exists():
            return None

        age = (
            time.time()
            - path.stat().st_mtime
        )

        if age > self.cache_ttl_seconds:
            return None

        try:
            with path.open(
                "r",
                encoding="utf-8",
            ) as file:
                return json.load(file)

        except Exception:
            return None

    def _write_cache(
        self,
        url,
        params,
        data,
    ):
        path = self._cache_path(
            url,
            params,
        )

        try:
            with path.open(
                "w",
                encoding="utf-8",
            ) as file:
                json.dump(
                    data,
                    file,
                    ensure_ascii=False,
                )

        except Exception:
            pass

    def _get(
        self,
        path,
        params=None,
        use_cache=True,
    ):
        url = f"{self.base_url}{path}"

        if use_cache:
            cached = self._read_cache(
                url,
                params,
            )

            if cached is not None:
                return cached

                last_error = None

        for attempt in range(6):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=(5, self.timeout),
                    headers={
                        "accept": "application/json",
                        "User-Agent": (
                            "Crypto-Research-Service"
                        ),
                        "Connection": "close",
                    },
                )

                if response.status_code == 429:
                    wait_seconds = 3 + attempt * 3
                    time.sleep(wait_seconds)
                    continue

                response.raise_for_status()
                data = response.json()

                if use_cache:
                    self._write_cache(
                        url,
                        params,
                        data,
                    )

                return data

            except requests.RequestException as exc:
                last_error = exc

                try:
                    self.session.close()
                except Exception:
                    pass

                self.session = requests.Session()

                if attempt < 5:
                    time.sleep(1)
                    continue

        raise RuntimeError(
            f"MEXC request failed: "
            f"{url} {params} | {last_error}"
        ) from last_error

    def ping(self):
        return self._get(
            "/api/v3/ping",
            use_cache=False,
        )

    def exchange_info(
        self,
        symbol=None,
        symbols=None,
    ):
        params = {}

        if symbol:
            params["symbol"] = symbol

        if symbols:
            if isinstance(symbols, (list, tuple, set)):
                params["symbols"] = ",".join(symbols)
            else:
                params["symbols"] = str(symbols)

        return self._get(
            "/api/v3/exchangeInfo",
            params=params or None,
            use_cache=True,
        )

    def ticker_24h_all(self):
        return self._get(
            "/api/v3/ticker/24hr",
            use_cache=False,
        )

    def ticker_24h(self, symbol):
        return self._get(
            "/api/v3/ticker/24hr",
            {
                "symbol": symbol,
            },
            use_cache=False,
        )

    def klines(
        self,
        symbol,
        interval,
        limit=500,
        start_time=None,
        end_time=None,
    ):
        limit = max(
            1,
            min(int(limit), 1000),
        )

        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }

        if start_time is not None:
            params["startTime"] = int(
                start_time
            )

        if end_time is not None:
            params["endTime"] = int(
                end_time
            )

        return self._get(
            "/api/v3/klines",
            params,
        )

    def depth(
        self,
        symbol,
        limit=100,
    ):
        return self._get(
            "/api/v3/depth",
            {
                "symbol": symbol,
                "limit": limit,
            },
            use_cache=False,
        )