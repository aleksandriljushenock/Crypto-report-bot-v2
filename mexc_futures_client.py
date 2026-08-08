import time
import requests

from market_errors import UnsupportedSymbolError


class MexcFuturesClient:
    def __init__(self, base_url="https://contract.mexc.com", timeout=15):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "crypto-report-bot/1.0", "Accept": "application/json"})

    @staticmethod
    def _contract(symbol):
        s = str(symbol).upper().replace("_", "").replace("-", "")
        if not s.endswith("USDT"):
            raise UnsupportedSymbolError(f"MEXC unsupported symbol: {symbol}")
        return f"{s[:-4]}_USDT"

    @staticmethod
    def _symbol(contract):
        return str(contract or "").replace("_", "").replace("-", "").upper()

    def _get(self, path, params=None):
        response = self.session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        if response.status_code == 404:
            raise UnsupportedSymbolError(f"MEXC market not found: {params or path}")
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success", False):
            code = payload.get("code")
            if code in {6004, 6005}:
                raise UnsupportedSymbolError(f"MEXC symbol unavailable: {params or path}")
            raise RuntimeError(f"MEXC error {code}: {payload.get('message') or payload.get('msg')}")
        return payload.get("data")

    @classmethod
    def _ticker_row(cls, row):
        last = float(row.get("lastPrice") or 0)
        change = float(row.get("riseFallRate") or 0) * 100.0
        return {
            "symbol": cls._symbol(row.get("symbol")),
            "lastPrice": str(last),
            "priceChangePercent": str(change),
            "quoteVolume": str(row.get("amount24") or 0),
            "highPrice": str(row.get("high24Price") or 0),
            "lowPrice": str(row.get("lower24Price") or 0),
            "count": 0,
            "lastFundingRate": str(row.get("fundingRate") or 0),
            "markPrice": str(row.get("fairPrice") or last),
            "indexPrice": str(row.get("indexPrice") or last),
            "openInterest": str(row.get("holdVol") or 0),
        }

    def exchange_info(self):
        rows = self._get("/api/v1/contract/detail") or []
        symbols = []
        for r in rows:
            if str(r.get("quoteCoin") or "").upper() != "USDT":
                continue
            symbols.append({
                "symbol": self._symbol(r.get("symbol")),
                "quoteAsset": "USDT",
                "contractType": "PERPETUAL",
                "status": "TRADING",
            })
        return {"symbols": symbols}

    def ticker_24h_all(self):
        rows = self._get("/api/v1/contract/ticker") or []
        if isinstance(rows, dict):
            rows = [rows]
        return [self._ticker_row(r) for r in rows if str(r.get("symbol", "")).upper().endswith("_USDT")]

    def ticker_24h(self, symbol):
        row = self._get("/api/v1/contract/ticker", {"symbol": self._contract(symbol)})
        if isinstance(row, list):
            row = row[0] if row else None
        if not row:
            raise UnsupportedSymbolError(f"MEXC ticker not found: {symbol}")
        return self._ticker_row(row)

    def klines(self, symbol, interval, limit):
        gran = {"5m": "Min5", "15m": "Min15", "1h": "Min60", "4h": "Hour4", "1d": "Day1"}.get(interval, interval)
        seconds = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}.get(interval, 3600)
        end = int(time.time())
        start = end - seconds * max(5, min(int(limit), 1000))
        data = self._get(f"/api/v1/contract/kline/{self._contract(symbol)}", {"interval": gran, "start": start, "end": end}) or {}
        times = data.get("time") or []
        out = []
        n = min(len(times), int(limit))
        start_idx = max(0, len(times) - n)
        for i in range(start_idx, len(times)):
            ts = int(times[i]) * 1000
            opn = data.get("open", [0] * len(times))[i]
            close = data.get("close", [0] * len(times))[i]
            high = data.get("high", [0] * len(times))[i]
            low = data.get("low", [0] * len(times))[i]
            vol = data.get("vol", [0] * len(times))[i]
            amount = data.get("amount", [0] * len(times))[i]
            out.append([ts, opn, high, low, close, vol, ts, amount, 0, 0, 0, "0"])
        return out

    def depth(self, symbol, limit=100):
        data = self._get(f"/api/v1/contract/depth/{self._contract(symbol)}", {"limit": min(int(limit), 100)}) or {}
        def norm(rows):
            out = []
            for r in rows or []:
                if isinstance(r, dict):
                    out.append([str(r.get("price") or r.get("p") or 0), str(r.get("vol") or r.get("quantity") or r.get("v") or 0)])
                else:
                    out.append([str(r[0]), str(r[1])])
            return out
        return {"bids": norm(data.get("bids")), "asks": norm(data.get("asks"))}

    def open_interest(self, symbol):
        t = self.ticker_24h(symbol)
        return {"symbol": symbol, "openInterest": t.get("openInterest", "0")}

    def open_interest_history(self, symbol, period="1h", limit=24):
        cur = self.open_interest(symbol)
        return [{"symbol": symbol, "sumOpenInterestValue": cur.get("openInterest", "0"), "timestamp": None}]

    def premium_index(self, symbol):
        t = self.ticker_24h(symbol)
        return {"symbol": symbol, "lastFundingRate": t.get("lastFundingRate", "0"), "markPrice": t.get("markPrice", t.get("lastPrice", "0")), "indexPrice": t.get("indexPrice", t.get("lastPrice", "0"))}

    def global_long_short_ratio(self, symbol, period="1h", limit=24):
        return [{"symbol": symbol, "longShortRatio": "1", "timestamp": None}]

    def taker_buy_sell_volume(self, symbol, period="1h", limit=24):
        return [{"buyVol": "1", "sellVol": "1"}]
