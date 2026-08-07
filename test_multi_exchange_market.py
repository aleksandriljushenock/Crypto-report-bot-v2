import os

import trade_market_client as tmc
from market_errors import UnsupportedSymbolError


class FakeVenue:
    def __init__(self, symbols, tickers, name="fake"):
        self.symbols = symbols
        self.tickers = tickers
        self.name = name
        self.calls = []

    def exchange_info(self):
        return {"symbols": [
            {"symbol": s, "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"}
            for s in self.symbols
        ]}

    def ticker_24h_all(self):
        return list(self.tickers)

    def ticker_24h(self, symbol):
        self.calls.append(("ticker_24h", symbol))
        for row in self.tickers:
            if row["symbol"] == symbol:
                return row
        raise UnsupportedSymbolError(symbol)


def _ticker(symbol, volume, price=10):
    return {"symbol": symbol, "quoteVolume": str(volume), "lastPrice": str(price), "priceChangePercent": "1", "highPrice": str(price*1.1), "lowPrice": str(price*0.9)}


def test_multi_exchange_universe_deduplicates_and_rewards_coverage(monkeypatch):
    venues = {
        "a": FakeVenue(["AAAUSDT", "BBBUSDT"], [_ticker("AAAUSDT", 100), _ticker("BBBUSDT", 95)]),
        "b": FakeVenue(["AAAUSDT", "CCCUSDT"], [_ticker("AAAUSDT", 90), _ticker("CCCUSDT", 99)]),
    }
    monkeypatch.setattr(tmc, "_provider_order", lambda: ["a", "b"])
    monkeypatch.setattr(tmc, "_build_provider", lambda name, timeout: venues[name])
    monkeypatch.setenv("MULTI_EXCHANGE_MIN_VENUES", "1")
    monkeypatch.setenv("MULTI_EXCHANGE_COVERAGE_BONUS", "0.08")

    rows, stats = tmc.collect_multi_exchange_universe(top_limit=3, min_quote_volume=0)

    assert [r["symbol"] for r in rows][0] == "AAAUSDT"
    aaa = next(r for r in rows if r["symbol"] == "AAAUSDT")
    assert aaa["exchangeCount"] == 2
    assert set(aaa["exchanges"]) == {"a", "b"}
    assert aaa["quoteVolume"] == 100
    assert stats["a"]["tradable"] == 2
    assert stats["b"]["tradable"] == 2


def test_min_venues_filters_single_venue_assets(monkeypatch):
    venues = {
        "a": FakeVenue(["AAAUSDT", "BBBUSDT"], [_ticker("AAAUSDT", 100), _ticker("BBBUSDT", 100)]),
        "b": FakeVenue(["AAAUSDT"], [_ticker("AAAUSDT", 80)]),
    }
    monkeypatch.setattr(tmc, "_provider_order", lambda: ["a", "b"])
    monkeypatch.setattr(tmc, "_build_provider", lambda name, timeout: venues[name])
    monkeypatch.setenv("MULTI_EXCHANGE_MIN_VENUES", "2")

    rows, _ = tmc.collect_multi_exchange_universe(top_limit=10, min_quote_volume=0)
    assert [r["symbol"] for r in rows] == ["AAAUSDT"]


def test_fallback_skips_known_unsupported_provider(monkeypatch):
    a = FakeVenue(["AAAUSDT"], [_ticker("AAAUSDT", 100)])
    b = FakeVenue(["BBBUSDT"], [_ticker("BBBUSDT", 100)])
    venues = {"a": a, "b": b}
    monkeypatch.setattr(tmc, "_build_provider", lambda name, timeout: venues[name])
    monkeypatch.setattr(tmc, "_provider_order", lambda: ["a", "b"])
    tmc._register_provider_symbols("a", {"AAAUSDT"})
    tmc._register_provider_symbols("b", {"BBBUSDT"})
    client = tmc.FallbackTradeMarketClient(providers=["a", "b"], timeout=1)

    row = client.ticker_24h("BBBUSDT")
    assert row["symbol"] == "BBBUSDT"
    assert ("ticker_24h", "BBBUSDT") not in a.calls
    assert ("ticker_24h", "BBBUSDT") in b.calls
