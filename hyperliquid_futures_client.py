import time
import requests

from market_errors import UnsupportedSymbolError


class HyperliquidFuturesClient:
    def __init__(self, base_url="https://api.hyperliquid.xyz", timeout=15):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "crypto-report-bot/1.0", "Content-Type": "application/json", "Accept": "application/json"})
        self._meta_cache = None
        self._meta_at = 0.0

    def _post(self, payload):
        response = self.session.post(f"{self.base_url}/info", json=payload, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _coin(symbol):
        s = str(symbol).upper().replace("-", "").replace("_", "")
        if not s.endswith("USDT"):
            raise UnsupportedSymbolError(f"Hyperliquid unsupported symbol: {symbol}")
        coin = s[:-4]
        return "BTC" if coin == "BTC" else coin

    @staticmethod
    def _symbol(coin):
        return f"{str(coin).upper()}USDT"

    def _meta(self, force=False):
        if self._meta_cache is None or force or time.time() - self._meta_at > 30:
            payload = self._post({"type": "metaAndAssetCtxs"})
            if not isinstance(payload, list) or len(payload) < 2:
                raise RuntimeError("Hyperliquid metaAndAssetCtxs malformed response")
            meta, ctxs = payload[0] or {}, payload[1] or []
            universe = meta.get("universe") or []
            rows = []
            for i, asset in enumerate(universe):
                coin = str(asset.get("name") or "")
                if not coin or asset.get("isDelisted"):
                    continue
                ctx = ctxs[i] if i < len(ctxs) else {}
                rows.append((coin, asset, ctx))
            self._meta_cache = rows
            self._meta_at = time.time()
        return self._meta_cache

    @classmethod
    def _ticker_row(cls, coin, ctx):
        last = float(ctx.get("markPx") or ctx.get("midPx") or ctx.get("oraclePx") or 0)
        prev = float(ctx.get("prevDayPx") or 0)
        pct = ((last / prev) - 1.0) * 100.0 if last and prev else 0.0
        return {"symbol": cls._symbol(coin), "lastPrice": str(last), "priceChangePercent": str(pct), "quoteVolume": str(ctx.get("dayNtlVlm") or 0), "highPrice": "0", "lowPrice": "0", "count": 0, "lastFundingRate": str(ctx.get("funding") or 0), "markPrice": str(ctx.get("markPx") or last), "indexPrice": str(ctx.get("oraclePx") or last), "openInterest": str(ctx.get("openInterest") or 0)}

    def exchange_info(self):
        return {"symbols": [{"symbol": self._symbol(coin), "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"} for coin, _, _ in self._meta()]}

    def ticker_24h_all(self):
        return [self._ticker_row(coin, ctx) for coin, _, ctx in self._meta(force=True)]

    def ticker_24h(self, symbol):
        target = self._coin(symbol)
        for coin, _, ctx in self._meta():
            if coin.upper() == target:
                return self._ticker_row(coin, ctx)
        raise UnsupportedSymbolError(f"Hyperliquid ticker not found: {symbol}")

    def klines(self, symbol, interval, limit):
        coin = self._coin(symbol)
        ms = {"5m": 300000, "15m": 900000, "1h": 3600000, "4h": 14400000, "1d": 86400000}.get(interval, 3600000)
        end = int(time.time() * 1000)
        start = end - ms * max(5, min(int(limit), 5000))
        rows = self._post({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": start, "endTime": end}}) or []
        out = []
        for r in rows:
            ts = int(r.get("t") or 0)
            close = float(r.get("c") or 0)
            vol = float(r.get("v") or 0)
            out.append([ts, r.get("o"), r.get("h"), r.get("l"), r.get("c"), r.get("v"), int(r.get("T") or ts), str(vol * close), int(r.get("n") or 0), 0, 0, "0"])
        out.sort(key=lambda x: int(x[0] or 0))
        return out[-int(limit):]

    def depth(self, symbol, limit=100):
        data = self._post({"type": "l2Book", "coin": self._coin(symbol)}) or {}
        levels = data.get("levels") or [[], []]
        conv = lambda rows: [[str(r.get("px") or 0), str(r.get("sz") or 0)] for r in rows[:min(int(limit), 20)]]
        return {"bids": conv(levels[0] if len(levels) > 0 else []), "asks": conv(levels[1] if len(levels) > 1 else [])}

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
