import requests

from market_errors import UnsupportedSymbolError
from candle_contract import normalize_candle


class BitgetFuturesClient:
    def __init__(self, base_url="https://api.bitget.com", timeout=15):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "crypto-report-bot/1.0"})

    def _get(self, path, params=None):
        response = self.session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("code")) != "00000":
            raise RuntimeError(f"Bitget error {payload.get('code')}: {payload.get('msg')}")
        return payload.get("data")

    @staticmethod
    def _ticker_row(row):
        last = float(row.get("lastPr") or 0)
        open24 = float(row.get("open24h") or 0)
        pct = float(row.get("change24h") or 0) * 100 if row.get("change24h") not in (None, "") else (((last-open24)/open24*100) if open24 else 0)
        return {
            "symbol": row.get("symbol"), "lastPrice": str(last), "priceChangePercent": str(pct),
            "quoteVolume": str(row.get("usdtVolume") or row.get("quoteVolume") or 0),
            "highPrice": str(row.get("high24h") or 0), "lowPrice": str(row.get("low24h") or 0), "count": 0,
            "lastFundingRate": row.get("fundingRate") or "0", "markPrice": row.get("markPrice") or row.get("lastPr") or "0",
            "indexPrice": row.get("indexPrice") or row.get("lastPr") or "0", "openInterest": row.get("holdingAmount") or "0",
        }

    def exchange_info(self):
        rows = self._get("/api/v2/mix/market/contracts", {"productType": "usdt-futures"}) or []
        return {"symbols": [{"symbol": r.get("symbol"), "quoteAsset": r.get("quoteCoin"), "contractType": "PERPETUAL", "status": "TRADING"} for r in rows if r.get("quoteCoin") == "USDT"]}

    def ticker_24h_all(self):
        rows = self._get("/api/v2/mix/market/tickers", {"productType": "usdt-futures"}) or []
        return [self._ticker_row(r) for r in rows if str(r.get("symbol", "")).endswith("USDT")]

    def ticker_24h(self, symbol):
        rows = self._get("/api/v2/mix/market/ticker", {"productType": "usdt-futures", "symbol": symbol}) or []
        if not rows:
            raise UnsupportedSymbolError(f"Bitget ticker not found: {symbol}")
        return self._ticker_row(rows[0])

    def klines(self, symbol, interval, limit):
        gran = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D"}.get(interval, interval)
        rows = self._get("/api/v2/mix/market/candles", {"symbol": symbol, "productType": "usdt-futures", "granularity": gran, "limit": min(int(limit), 1000)}) or []
        out = []
        for item in reversed(rows):
            ts, opn, high, low, close, base_vol, quote_vol = item[:7]
            out.append(normalize_candle(ts, opn, high, low, close, base_vol, interval=interval, quote_volume=quote_vol))
        return out

    def depth(self, symbol, limit=100):
        data = self._get("/api/v3/market/orderbook", {"category": "USDT-FUTURES", "symbol": symbol, "limit": min(int(limit), 1000)}) or {}
        if not data:
            raise UnsupportedSymbolError(f"Bitget order book not found: {symbol}")
        return {"bids": data.get("b", []), "asks": data.get("a", [])}

    def open_interest(self, symbol):
        data = self._get("/api/v2/mix/market/open-interest", {"symbol": symbol, "productType": "usdt-futures"}) or {}
        rows = data.get("openInterestList") or []
        return {"symbol": symbol, "openInterest": rows[0].get("size", "0") if rows else "0"}

    def open_interest_history(self, symbol, period="1h", limit=24):
        current = self.open_interest(symbol)
        return [{"symbol": symbol, "sumOpenInterestValue": current.get("openInterest", "0"), "timestamp": None}]

    def premium_index(self, symbol):
        ticker = self.ticker_24h(symbol)
        return {"symbol": symbol, "lastFundingRate": ticker.get("lastFundingRate", "0"), "markPrice": ticker.get("markPrice", ticker.get("lastPrice", "0")), "indexPrice": ticker.get("indexPrice", ticker.get("lastPrice", "0"))}

    def global_long_short_ratio(self, symbol, period="1h", limit=24):
        rows = self._get("/api/v2/mix/market/account-long-short", {"symbol": symbol, "period": period}) or []
        return [{"symbol": symbol, "longShortRatio": r.get("longShortAccountRatio") or "1", "timestamp": r.get("ts")} for r in rows[:int(limit)]]

    def taker_buy_sell_volume(self, symbol, period="1h", limit=24):
        # Keep neutral until Bitget's initiative buy/sell endpoint is normalized.
        return [{"buyVol": "1", "sellVol": "1"}]
