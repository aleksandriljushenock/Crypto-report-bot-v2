import requests

from market_errors import UnsupportedSymbolError
from candle_contract import normalize_candle


class BingxFuturesClient:
    def __init__(self, base_url="https://open-api.bingx.com", timeout=15):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "crypto-report-bot/1.0", "Accept": "application/json"})

    @staticmethod
    def _contract(symbol):
        s = str(symbol).upper().replace("-", "").replace("_", "")
        if not s.endswith("USDT"):
            raise UnsupportedSymbolError(f"BingX unsupported symbol: {symbol}")
        return f"{s[:-4]}-USDT"

    @staticmethod
    def _symbol(contract):
        return str(contract or "").replace("-", "").replace("_", "").upper()

    def _get(self, path, params=None):
        response = self.session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        if response.status_code == 404:
            raise UnsupportedSymbolError(f"BingX market not found: {params or path}")
        response.raise_for_status()
        payload = response.json()
        code = payload.get("code", 0)
        if str(code) not in {"0", "200", "200000"}:
            msg = payload.get("msg") or payload.get("message")
            if "symbol" in str(msg).lower() or "contract" in str(msg).lower():
                raise UnsupportedSymbolError(f"BingX symbol unavailable: {msg}")
            raise RuntimeError(f"BingX error {code}: {msg}")
        return payload.get("data")

    @classmethod
    def _ticker_row(cls, row):
        last = float(row.get("lastPrice") or row.get("last") or row.get("closePrice") or 0)
        pct = row.get("priceChangePercent")
        if pct in (None, ""):
            rate = row.get("priceChangeRate")
            pct = float(rate or 0) * 100.0
        return {
            "symbol": cls._symbol(row.get("symbol")),
            "lastPrice": str(last),
            "priceChangePercent": str(pct or 0),
            "quoteVolume": str(row.get("quoteVolume") or row.get("turnover") or row.get("amount") or 0),
            "highPrice": str(row.get("highPrice") or row.get("high24h") or 0),
            "lowPrice": str(row.get("lowPrice") or row.get("low24h") or 0),
            "count": 0,
            "lastFundingRate": str(row.get("fundingRate") or 0),
            "markPrice": str(row.get("markPrice") or last),
            "indexPrice": str(row.get("indexPrice") or last),
            "openInterest": str(row.get("openInterest") or 0),
        }

    def exchange_info(self):
        rows = self._get("/openApi/swap/v2/quote/contracts") or []
        if isinstance(rows, dict):
            rows = rows.get("contracts") or rows.get("list") or []
        return {"symbols": [{"symbol": self._symbol(r.get("symbol")), "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"} for r in rows if str(r.get("symbol", "")).upper().endswith("-USDT") and str(r.get("status", "1")).lower() not in {"0", "offline", "suspend"}]}

    def ticker_24h_all(self):
        rows = self._get("/openApi/swap/v2/quote/ticker") or []
        if isinstance(rows, dict):
            rows = rows.get("tickers") or rows.get("list") or [rows]
        return [self._ticker_row(r) for r in rows if str(r.get("symbol", "")).upper().endswith("-USDT")]

    def ticker_24h(self, symbol):
        data = self._get("/openApi/swap/v2/quote/ticker", {"symbol": self._contract(symbol)})
        if isinstance(data, list):
            data = data[0] if data else None
        if not data:
            raise UnsupportedSymbolError(f"BingX ticker not found: {symbol}")
        return self._ticker_row(data)

    def klines(self, symbol, interval, limit):
        gran = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}.get(interval, interval)
        rows = self._get("/openApi/swap/v3/quote/klines", {"symbol": self._contract(symbol), "interval": gran, "limit": min(int(limit), 1000)}) or []
        if isinstance(rows, dict):
            rows = rows.get("klines") or rows.get("list") or []
        out = []
        for r in rows:
            if isinstance(r, dict):
                ts = int(r.get("time") or r.get("openTime") or r.get("timestamp") or 0)
                out.append(normalize_candle(ts, r.get("open"), r.get("high"), r.get("low"), r.get("close"), r.get("volume") or 0, interval=interval, quote_volume=r.get("quoteVolume") or r.get("turnover") or 0))
            else:
                vals = list(r)
                if len(vals) >= 7:
                    out.append(vals[:12] + ["0"] * max(0, 12 - len(vals)))
        out.sort(key=lambda x: int(x[0] or 0))
        return out[-int(limit):]

    def depth(self, symbol, limit=100):
        data = self._get("/openApi/swap/v2/quote/depth", {"symbol": self._contract(symbol), "limit": min(int(limit), 1000)}) or {}
        return {"bids": data.get("bids") or [], "asks": data.get("asks") or []}

    def open_interest(self, symbol):
        data = self._get("/openApi/swap/v2/quote/openInterest", {"symbol": self._contract(symbol)}) or {}
        return {"symbol": symbol, "openInterest": str(data.get("openInterest") or data.get("openInterestValue") or 0)}

    def open_interest_history(self, symbol, period="1h", limit=24):
        cur = self.open_interest(symbol)
        return [{"symbol": symbol, "sumOpenInterestValue": cur.get("openInterest", "0"), "timestamp": None}]

    def premium_index(self, symbol):
        data = self._get("/openApi/swap/v2/quote/premiumIndex", {"symbol": self._contract(symbol)}) or {}
        return {"symbol": symbol, "lastFundingRate": str(data.get("lastFundingRate") or data.get("fundingRate") or 0), "markPrice": str(data.get("markPrice") or 0), "indexPrice": str(data.get("indexPrice") or 0)}

    def global_long_short_ratio(self, symbol, period="1h", limit=24):
        return [{"symbol": symbol, "longShortRatio": "1", "timestamp": None}]

    def taker_buy_sell_volume(self, symbol, period="1h", limit=24):
        return [{"buyVol": "1", "sellVol": "1"}]
