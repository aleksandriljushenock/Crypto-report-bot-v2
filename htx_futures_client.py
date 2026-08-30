import requests

from market_errors import UnsupportedSymbolError
from candle_contract import normalize_candle


class HtxFuturesClient:
    def __init__(self, base_url="https://api.hbdm.com", timeout=15):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "crypto-report-bot/1.0", "Accept": "application/json"})

    @staticmethod
    def _contract(symbol):
        s = str(symbol).upper().replace("-", "").replace("_", "")
        if not s.endswith("USDT"):
            raise UnsupportedSymbolError(f"HTX unsupported symbol: {symbol}")
        return f"{s[:-4]}-USDT"

    @staticmethod
    def _symbol(contract):
        return str(contract or "").replace("-", "").replace("_", "").upper()

    def _get(self, path, params=None):
        response = self.session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        if response.status_code == 404:
            raise UnsupportedSymbolError(f"HTX market not found: {params or path}")
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("status", "ok")).lower() not in {"ok", "200"}:
            raise RuntimeError(f"HTX error {payload.get('err_code') or payload.get('err-code')}: {payload.get('err_msg') or payload.get('err-msg')}")
        return payload.get("data") if "data" in payload else payload.get("ticks") or payload.get("tick")

    @classmethod
    def _ticker_row(cls, row):
        last = float(row.get("close") or row.get("last_price") or row.get("lastPrice") or 0)
        open24 = float(row.get("open") or 0)
        pct = ((last / open24) - 1.0) * 100.0 if last and open24 else float(row.get("price_change_percent") or 0)
        quote = row.get("trade_turnover") or row.get("turnover") or row.get("quote_volume")
        if quote in (None, ""):
            quote = float(row.get("amount") or 0) * last
        return {"symbol": cls._symbol(row.get("contract_code") or row.get("symbol")), "lastPrice": str(last), "priceChangePercent": str(pct), "quoteVolume": str(quote or 0), "highPrice": str(row.get("high") or 0), "lowPrice": str(row.get("low") or 0), "count": int(row.get("count") or 0), "lastFundingRate": str(row.get("funding_rate") or 0), "markPrice": str(row.get("mark_price") or last), "indexPrice": str(row.get("index_price") or last), "openInterest": str(row.get("open_interest") or 0)}

    def exchange_info(self):
        rows = self._get("/linear-swap-api/v1/swap_contract_info") or []
        if isinstance(rows, dict):
            rows = [rows]
        out = []
        for r in rows:
            code = str(r.get("contract_code") or "")
            if not code.upper().endswith("-USDT"):
                continue
            status = int(r.get("contract_status") or 1)
            if status not in {1, 2}:
                continue
            out.append({"symbol": self._symbol(code), "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"})
        return {"symbols": out}

    def ticker_24h_all(self):
        response = self.session.get(f"{self.base_url}/linear-swap-ex/market/detail/batch_merged", timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("status", "ok")).lower() != "ok":
            raise RuntimeError(f"HTX ticker error: {payload}")
        rows = payload.get("ticks") or payload.get("data") or []
        return [self._ticker_row(r) for r in rows if str(r.get("contract_code") or r.get("symbol") or "").upper().endswith("-USDT")]

    def ticker_24h(self, symbol):
        response = self.session.get(f"{self.base_url}/linear-swap-ex/market/detail/merged", params={"contract_code": self._contract(symbol)}, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("status", "ok")).lower() != "ok" or not payload.get("tick"):
            raise UnsupportedSymbolError(f"HTX ticker not found: {symbol}")
        row = dict(payload.get("tick") or {})
        row["contract_code"] = self._contract(symbol)
        return self._ticker_row(row)

    def klines(self, symbol, interval, limit):
        period = {"1m": "1min", "5m": "5min", "15m": "15min", "1h": "60min", "4h": "4hour", "1d": "1day"}.get(interval, interval)
        rows = self._get("/linear-swap-ex/market/history/kline", {"contract_code": self._contract(symbol), "period": period, "size": min(int(limit), 2000)}) or []
        out = []
        for r in rows:
            ts = int(r.get("id") or 0) * 1000
            close = float(r.get("close") or 0)
            base = float(r.get("amount") or 0)
            quote = r.get("trade_turnover") or (base * close)
            out.append(normalize_candle(ts, r.get("open"), r.get("high"), r.get("low"), r.get("close"), r.get("amount") or r.get("vol") or 0, interval=interval, quote_volume=quote, trade_count=int(r.get("count") or 0)))
        out.sort(key=lambda x: int(x[0] or 0))
        return out[-int(limit):]

    def depth(self, symbol, limit=100):
        data = self._get("/linear-swap-ex/market/depth", {"contract_code": self._contract(symbol), "type": "step0"}) or {}
        return {"bids": (data.get("bids") or [])[:int(limit)], "asks": (data.get("asks") or [])[:int(limit)]}

    def open_interest(self, symbol):
        rows = self._get("/linear-swap-api/v1/swap_open_interest", {"contract_code": self._contract(symbol)}) or []
        if isinstance(rows, dict):
            rows = [rows]
        val = rows[0].get("volume") if rows else 0
        return {"symbol": symbol, "openInterest": str(val or 0)}

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
