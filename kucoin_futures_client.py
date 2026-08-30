import time
import requests

from market_errors import UnsupportedSymbolError
from candle_contract import normalize_candle


class KucoinFuturesClient:
    def __init__(self, base_url="https://api-futures.kucoin.com", timeout=15):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "crypto-report-bot/1.0", "Accept": "application/json"})
        self._contracts = None
        self._contracts_at = 0.0

    def _get(self, path, params=None):
        response = self.session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        if response.status_code == 404:
            raise UnsupportedSymbolError(f"KuCoin market not found: {params or path}")
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("code")) != "200000":
            raise RuntimeError(f"KuCoin error {payload.get('code')}: {payload.get('msg')}")
        return payload.get("data")

    @staticmethod
    def _normal_symbol(row):
        base = str(row.get("baseCurrency") or row.get("baseCoin") or "").upper()
        quote = str(row.get("quoteCurrency") or row.get("quoteCoin") or "").upper()
        if base == "XBT":
            base = "BTC"
        if not base:
            raw = str(row.get("symbol") or "").upper()
            if raw == "XBTUSDTM":
                return "BTCUSDT"
            if raw.endswith("USDTM"):
                return raw[:-5] + "USDT"
        return f"{base}{quote}" if base and quote else ""

    def _active(self, force=False):
        if self._contracts is None or force or time.time() - self._contracts_at > 120:
            rows = self._get("/api/v1/contracts/active") or []
            if isinstance(rows, dict):
                rows = [rows]
            self._contracts = rows
            self._contracts_at = time.time()
        return self._contracts

    def _contract(self, symbol):
        target = str(symbol).upper().replace("-", "").replace("_", "")
        for row in self._active():
            if self._normal_symbol(row) == target:
                return str(row.get("symbol"))
        raise UnsupportedSymbolError(f"KuCoin ticker not found: {symbol}")

    @classmethod
    def _ticker_row(cls, row):
        last = float(row.get("lastTradePrice") or row.get("lastPrice") or row.get("price") or 0)
        pct = row.get("priceChgPct")
        pct = float(pct or 0) * 100.0
        qv = row.get("turnoverOf24h") or row.get("turnover") or row.get("quoteVolume") or 0
        return {"symbol": cls._normal_symbol(row), "lastPrice": str(last), "priceChangePercent": str(pct), "quoteVolume": str(qv), "highPrice": str(row.get("highPrice") or 0), "lowPrice": str(row.get("lowPrice") or 0), "count": 0, "lastFundingRate": str(row.get("fundingFeeRate") or row.get("fundingRate") or 0), "markPrice": str(row.get("markPrice") or last), "indexPrice": str(row.get("indexPrice") or last), "openInterest": str(row.get("openInterest") or 0)}

    def exchange_info(self):
        rows = self._active()
        out = []
        for r in rows:
            symbol = self._normal_symbol(r)
            if not symbol.endswith("USDT"):
                continue
            status = str(r.get("status") or "Open").lower()
            if status in {"closed", "settled", "disabled"}:
                continue
            out.append({"symbol": symbol, "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"})
        return {"symbols": out}

    def ticker_24h_all(self):
        return [self._ticker_row(r) for r in self._active(force=True) if self._normal_symbol(r).endswith("USDT")]

    def ticker_24h(self, symbol):
        contract = self._contract(symbol)
        data = self._get(f"/api/v1/contracts/{contract}") or {}
        return self._ticker_row(data)

    def klines(self, symbol, interval, limit):
        gran = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}.get(interval)
        if gran is None:
            raise ValueError(f"Unsupported KuCoin candle interval: {interval}")
        end = int(time.time() * 1000)
        start = end - gran * 1000 * max(5, min(int(limit), 500))
        rows = self._get("/api/v1/kline/query", {"symbol": self._contract(symbol), "granularity": gran, "from": start, "to": end}) or []
        out = []
        for r in rows:
            if len(r) < 7:
                continue
            ts, opn, high, low, close, vol, turnover = r[:7]
            out.append(normalize_candle(ts, opn, high, low, close, vol, interval=interval, quote_volume=turnover))
        out.sort(key=lambda x: int(x[0] or 0))
        return out[-int(limit):]

    def depth(self, symbol, limit=100):
        data = self._get("/api/v1/level2/snapshot", {"symbol": self._contract(symbol)}) or {}
        return {"bids": (data.get("bids") or [])[:int(limit)], "asks": (data.get("asks") or [])[:int(limit)]}

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
