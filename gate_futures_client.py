import requests

from market_errors import UnsupportedSymbolError
from candle_contract import normalize_candle


class GateFuturesClient:
    def __init__(self, base_url="https://api.gateio.ws/api/v4", timeout=15):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "crypto-report-bot/1.0", "Accept": "application/json"})

    @staticmethod
    def _contract(symbol):
        s = str(symbol).upper().replace("_", "")
        if not s.endswith("USDT"):
            raise UnsupportedSymbolError(f"Gate unsupported symbol: {symbol}")
        return f"{s[:-4]}_USDT"

    @staticmethod
    def _symbol(contract):
        return str(contract).replace("_", "")

    def _get(self, path, params=None):
        response = self.session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        if response.status_code == 404:
            raise UnsupportedSymbolError(f"Gate market not found: {params or path}")
        response.raise_for_status()
        return response.json()

    @classmethod
    def _ticker_row(cls, row):
        last = float(row.get("last") or 0)
        change = float(row.get("change_percentage") or 0)
        return {"symbol": cls._symbol(row.get("contract")), "lastPrice": str(last), "priceChangePercent": str(change), "quoteVolume": str(row.get("volume_24h_quote") or row.get("volume_24h_usd") or 0), "highPrice": str(row.get("high_24h") or 0), "lowPrice": str(row.get("low_24h") or 0), "count": 0, "lastFundingRate": row.get("funding_rate") or "0", "markPrice": row.get("mark_price") or row.get("last") or "0", "indexPrice": row.get("index_price") or row.get("last") or "0", "openInterest": row.get("total_size") or "0"}

    def exchange_info(self):
        rows = self._get("/futures/usdt/contracts") or []
        return {"symbols": [{"symbol": self._symbol(r.get("name")), "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING" if not r.get("in_delisting") else "DELISTING"} for r in rows if str(r.get("name", "")).endswith("_USDT")]}

    def ticker_24h_all(self):
        return [self._ticker_row(r) for r in (self._get("/futures/usdt/tickers") or []) if str(r.get("contract", "")).endswith("_USDT")]

    def ticker_24h(self, symbol):
        rows = self._get("/futures/usdt/tickers", {"contract": self._contract(symbol)}) or []
        if not rows:
            raise UnsupportedSymbolError(f"Gate ticker not found: {symbol}")
        return self._ticker_row(rows[0])

    def klines(self, symbol, interval, limit):
        gran = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}.get(interval, interval)
        rows = self._get("/futures/usdt/candlesticks", {"contract": self._contract(symbol), "interval": gran, "limit": min(int(limit), 2000)}) or []
        out = []
        for r in rows:
            ts = int(r.get("t") or 0) * 1000
            out.append(normalize_candle(ts, r.get("o"), r.get("h"), r.get("l"), r.get("c"), r.get("v"), interval=interval, quote_volume=r.get("sum") or 0))
        return out

    def depth(self, symbol, limit=100):
        data = self._get("/futures/usdt/order_book", {"contract": self._contract(symbol), "limit": min(int(limit), 100)}) or {}
        return {"bids": [[str(r.get("p")), str(r.get("s"))] for r in data.get("bids", [])], "asks": [[str(r.get("p")), str(r.get("s"))] for r in data.get("asks", [])]}

    def open_interest(self, symbol):
        row = self._get(f"/futures/usdt/contracts/{self._contract(symbol)}") or {}
        return {"symbol": symbol, "openInterest": str(row.get("open_interest") or row.get("total_size") or 0)}

    def open_interest_history(self, symbol, period="1h", limit=24):
        rows = self._get("/futures/usdt/contract_stats", {"contract": self._contract(symbol), "interval": period, "limit": min(int(limit), 100)}) or []
        return [{"symbol": symbol, "sumOpenInterestValue": str(r.get("open_interest_usd") or r.get("open_interest") or 0), "timestamp": int(r.get("time") or 0) * 1000} for r in rows]

    def premium_index(self, symbol):
        t = self.ticker_24h(symbol)
        return {"symbol": symbol, "lastFundingRate": t.get("lastFundingRate", "0"), "markPrice": t.get("markPrice", t.get("lastPrice", "0")), "indexPrice": t.get("indexPrice", t.get("lastPrice", "0"))}

    def global_long_short_ratio(self, symbol, period="1h", limit=24):
        rows = self._get("/futures/usdt/contract_stats", {"contract": self._contract(symbol), "interval": period, "limit": min(int(limit), 100)}) or []
        return [{"symbol": symbol, "longShortRatio": str(r.get("lsr_account") or 1), "timestamp": int(r.get("time") or 0) * 1000} for r in rows]

    def taker_buy_sell_volume(self, symbol, period="1h", limit=24):
        rows = self._get("/futures/usdt/contract_stats", {"contract": self._contract(symbol), "interval": period, "limit": min(int(limit), 100)}) or []
        # Gate exposes a taker long/short ratio, not absolute buy/sell volumes. Encode as a ratio-preserving pair.
        out = []
        for r in rows:
            ratio = float(r.get("lsr_taker") or 1)
            out.append({"buyVol": str(max(ratio, 0.000001)), "sellVol": "1"})
        return out or [{"buyVol": "1", "sellVol": "1"}]
