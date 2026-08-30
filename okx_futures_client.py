import requests

from market_errors import UnsupportedSymbolError
from candle_contract import normalize_candle


class OkxFuturesClient:
    def __init__(self, base_url="https://www.okx.com", timeout=15):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "crypto-report-bot/1.0"})

    def _get(self, path, params=None):
        response = self.session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("code", "0")) != "0":
            raise RuntimeError(f"OKX error {payload.get('code')}: {payload.get('msg')}")
        return payload.get("data") or []

    @staticmethod
    def _inst_id(symbol):
        s = str(symbol).upper().replace("-", "")
        if not s.endswith("USDT"):
            raise UnsupportedSymbolError(f"OKX unsupported symbol: {symbol}")
        return f"{s[:-4]}-USDT-SWAP"

    @staticmethod
    def _symbol(inst_id):
        return str(inst_id).replace("-USDT-SWAP", "USDT").replace("-", "")

    @classmethod
    def _ticker_row(cls, row):
        last = float(row.get("last") or 0)
        open24 = float(row.get("open24h") or 0)
        pct = ((last - open24) / open24 * 100) if open24 else 0.0
        return {
            "symbol": cls._symbol(row.get("instId")),
            "lastPrice": str(last),
            "priceChangePercent": str(pct),
            "quoteVolume": str(float(row.get("volCcy24h") or 0) * last),
            "highPrice": str(row.get("high24h") or 0),
            "lowPrice": str(row.get("low24h") or 0),
            "count": 0,
        }

    def exchange_info(self):
        rows = self._get("/api/v5/public/instruments", {"instType": "SWAP"})
        return {"symbols": [
            {
                "symbol": self._symbol(row.get("instId")),
                "quoteAsset": "USDT",
                "contractType": "PERPETUAL",
                "status": "TRADING" if row.get("state") == "live" else str(row.get("state") or "").upper(),
            }
            for row in rows if str(row.get("instId", "")).endswith("-USDT-SWAP")
        ]}

    def ticker_24h_all(self):
        rows = self._get("/api/v5/market/tickers", {"instType": "SWAP"})
        return [self._ticker_row(row) for row in rows if str(row.get("instId", "")).endswith("-USDT-SWAP")]

    def ticker_24h(self, symbol):
        rows = self._get("/api/v5/market/ticker", {"instId": self._inst_id(symbol)})
        if not rows:
            raise UnsupportedSymbolError(f"OKX ticker not found: {symbol}")
        return self._ticker_row(rows[0])

    def klines(self, symbol, interval, limit):
        bars = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D"}
        rows = self._get("/api/v5/market/candles", {
            "instId": self._inst_id(symbol), "bar": bars.get(interval, interval), "limit": min(int(limit), 300)
        })
        out = []
        for item in reversed(rows):
            ts, opn, high, low, close, vol, vol_ccy, vol_quote = item[:8]
            out.append(normalize_candle(ts, opn, high, low, close, vol, interval=interval, quote_volume=vol_quote))
        return out

    def depth(self, symbol, limit=100):
        rows = self._get("/api/v5/market/books", {"instId": self._inst_id(symbol), "sz": min(int(limit), 400)})
        if not rows:
            raise UnsupportedSymbolError(f"OKX order book not found: {symbol}")
        return {"bids": rows[0].get("bids", []), "asks": rows[0].get("asks", [])}

    def open_interest(self, symbol):
        rows = self._get("/api/v5/public/open-interest", {"instType": "SWAP", "instId": self._inst_id(symbol)})
        if not rows:
            return {"symbol": symbol, "openInterest": "0"}
        return {"symbol": symbol, "openInterest": rows[0].get("oiCcy") or rows[0].get("oi") or "0"}

    def open_interest_history(self, symbol, period="1h", limit=24):
        base = str(symbol).upper().replace("USDT", "")
        bars = {"5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H", "4h": "4H", "1d": "1D"}
        try:
            rows = self._get("/api/v5/rubik/stat/contracts/open-interest-history", {
                "ccy": base, "period": bars.get(period, "1H")
            })
            return [
                {"symbol": symbol, "sumOpenInterestValue": row[1] if isinstance(row, list) and len(row) > 1 else "0", "timestamp": row[0] if isinstance(row, list) else None}
                for row in rows[-int(limit):]
            ]
        except Exception:
            current = self.open_interest(symbol)
            return [{"symbol": symbol, "sumOpenInterestValue": current.get("openInterest", "0"), "timestamp": None}]

    def premium_index(self, symbol):
        inst = self._inst_id(symbol)
        ticker = self.ticker_24h(symbol)
        mark = ticker.get("lastPrice", "0")
        index = mark
        funding = "0"
        try:
            rows = self._get("/api/v5/public/funding-rate", {"instId": inst})
            if rows:
                funding = rows[0].get("fundingRate") or "0"
        except Exception:
            pass
        try:
            rows = self._get("/api/v5/public/mark-price", {"instType": "SWAP", "instId": inst})
            if rows:
                mark = rows[0].get("markPx") or mark
        except Exception:
            pass
        return {"symbol": symbol, "lastFundingRate": funding, "markPrice": mark, "indexPrice": index}

    def global_long_short_ratio(self, symbol, period="1h", limit=24):
        base = str(symbol).upper().replace("USDT", "")
        bars = {"5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H", "4h": "4H", "1d": "1D"}
        try:
            rows = self._get("/api/v5/rubik/stat/contracts/long-short-account-ratio", {"ccy": base, "period": bars.get(period, "1H")})
            return [
                {"symbol": symbol, "longShortRatio": str(row[1]), "timestamp": row[0]}
                for row in rows[-int(limit):] if isinstance(row, list) and len(row) > 1
            ]
        except Exception:
            return []

    def taker_buy_sell_volume(self, symbol, period="1h", limit=24):
        # Neutral fallback; OKX public taker-volume payload is not Binance-shaped.
        return [{"buyVol": "1", "sellVol": "1"}]
