import json
import time
from pathlib import Path
import hashlib
import requests


class BybitFuturesClient:
    """Bybit V5 linear-perpetual adapter returning Binance-shaped payloads.

    This keeps the existing analyzer unchanged while avoiding direct Binance
    Futures calls from cloud providers whose IP ranges are blocked by Binance.
    """

    def __init__(self, base_url="https://api.bybit.com", timeout=15, cache_ttl_seconds=180):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.cache_ttl_seconds = cache_ttl_seconds
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "crypto-report-bot/1.0"})
        self.cache_dir = Path("cache")
        self.cache_dir.mkdir(exist_ok=True)

    def _cache_key(self, path, params):
        raw = path + "|" + json.dumps(params or {}, sort_keys=True)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _cache_path(self, path, params):
        return self.cache_dir / f"bybit_{self._cache_key(path, params)}.json"

    def _read_cache(self, path, params):
        target = self._cache_path(path, params)
        if not target.exists() or time.time() - target.stat().st_mtime > self.cache_ttl_seconds:
            return None
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _write_cache(self, path, params, data):
        try:
            self._cache_path(path, params).write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass

    def _get(self, path, params=None, use_cache=True):
        if use_cache:
            cached = self._read_cache(path, params)
            if cached is not None:
                return cached
        url = f"{self.base_url}{path}"
        last_error = None
        for attempt in range(3):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                if response.status_code in (429, 500, 502, 503, 504):
                    time.sleep(1.5 * (attempt + 1))
                    continue
                response.raise_for_status()
                payload = response.json()
                if int(payload.get("retCode", -1)) != 0:
                    raise RuntimeError(f"Bybit error {payload.get('retCode')}: {payload.get('retMsg')}")
                result = payload.get("result") or {}
                if use_cache:
                    self._write_cache(path, params, result)
                return result
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(1 + attempt)
        raise RuntimeError(f"Request failed: {url} {params} | {last_error}")

    @staticmethod
    def _ticker_row(row):
        last = float(row.get("lastPrice") or 0)
        prev = float(row.get("prevPrice24h") or 0)
        pct = ((last - prev) / prev * 100) if prev else float(row.get("price24hPcnt") or 0) * 100
        high = float(row.get("highPrice24h") or 0)
        low = float(row.get("lowPrice24h") or 0)
        turnover = float(row.get("turnover24h") or 0)
        return {
            "symbol": row.get("symbol"),
            "lastPrice": str(last),
            "priceChangePercent": str(pct),
            "quoteVolume": str(turnover),
            "highPrice": str(high),
            "lowPrice": str(low),
            "count": 0,
            "lastFundingRate": row.get("fundingRate") or "0",
            "markPrice": row.get("markPrice") or row.get("lastPrice") or "0",
            "indexPrice": row.get("indexPrice") or row.get("lastPrice") or "0",
            "openInterest": row.get("openInterest") or "0",
        }

    def exchange_info(self):
        result = self._get("/v5/market/instruments-info", {"category": "linear", "limit": 1000})
        symbols = []
        for row in result.get("list", []):
            if row.get("quoteCoin") != "USDT" or row.get("contractType") not in ("LinearPerpetual", ""):
                continue
            symbols.append({
                "symbol": row.get("symbol"),
                "quoteAsset": row.get("quoteCoin"),
                "contractType": "PERPETUAL",
                "status": "TRADING" if row.get("status") == "Trading" else row.get("status", ""),
            })
        return {"symbols": symbols}

    def ticker_24h_all(self):
        result = self._get("/v5/market/tickers", {"category": "linear"}, use_cache=False)
        return [self._ticker_row(row) for row in result.get("list", []) if row.get("symbol", "").endswith("USDT")]

    def ticker_24h(self, symbol):
        result = self._get("/v5/market/tickers", {"category": "linear", "symbol": symbol}, use_cache=False)
        rows = result.get("list", [])
        if not rows:
            raise RuntimeError(f"Bybit ticker not found: {symbol}")
        return self._ticker_row(rows[0])

    def klines(self, symbol, interval, limit):
        interval_map = {"5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "D"}
        result = self._get("/v5/market/kline", {
            "category": "linear", "symbol": symbol,
            "interval": interval_map.get(interval, interval), "limit": min(int(limit), 1000),
        })
        rows = []
        for item in reversed(result.get("list", [])):
            # Bybit: start, open, high, low, close, volume, turnover
            start, opn, high, low, close, volume, turnover = item[:7]
            rows.append([start, opn, high, low, close, volume, start, turnover, 0, 0, 0, "0"])
        return rows

    def depth(self, symbol, limit=100):
        result = self._get("/v5/market/orderbook", {
            "category": "linear", "symbol": symbol, "limit": min(int(limit), 200),
        }, use_cache=False)
        return {"bids": result.get("b", []), "asks": result.get("a", [])}

    def open_interest(self, symbol):
        result = self._get("/v5/market/open-interest", {
            "category": "linear", "symbol": symbol, "intervalTime": "1h", "limit": 1,
        }, use_cache=False)
        rows = result.get("list", [])
        return {"symbol": symbol, "openInterest": rows[0].get("openInterest", "0") if rows else "0"}

    def open_interest_history(self, symbol, period="1h", limit=24):
        period_map = {"5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h", "1d": "1d"}
        result = self._get("/v5/market/open-interest", {
            "category": "linear", "symbol": symbol,
            "intervalTime": period_map.get(period, "1h"), "limit": min(int(limit), 200),
        })
        return [{"symbol": symbol, "sumOpenInterestValue": row.get("openInterest", "0"), "timestamp": row.get("timestamp")} for row in reversed(result.get("list", []))]

    def premium_index(self, symbol):
        ticker = self.ticker_24h(symbol)
        return {
            "symbol": symbol,
            "lastFundingRate": ticker.get("lastFundingRate", "0"),
            "markPrice": ticker.get("markPrice", ticker.get("lastPrice", "0")),
            "indexPrice": ticker.get("indexPrice", ticker.get("lastPrice", "0")),
        }

    def global_long_short_ratio(self, symbol, period="1h", limit=24):
        period_map = {"5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h", "1d": "1d"}
        try:
            result = self._get("/v5/market/account-ratio", {
                "category": "linear", "symbol": symbol,
                "period": period_map.get(period, "1h"), "limit": min(int(limit), 500),
            })
            rows = []
            for row in reversed(result.get("list", [])):
                buy = float(row.get("buyRatio") or 0)
                sell = float(row.get("sellRatio") or 0)
                rows.append({"symbol": symbol, "longShortRatio": str(buy / sell if sell else 1.0), "timestamp": row.get("timestamp")})
            return rows
        except Exception:
            return []

    def taker_buy_sell_volume(self, symbol, period="1h", limit=24):
        # Bybit does not expose a Binance-compatible public taker buy/sell history.
        # Neutral values preserve scoring without falsely biasing a direction.
        return [{"buyVol": "1", "sellVol": "1"}]
