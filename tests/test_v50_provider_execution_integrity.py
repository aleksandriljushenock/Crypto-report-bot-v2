from datetime import datetime, timedelta, timezone

import pytest

from candle_contract import candle_end_ms, normalize_existing_rows


def _assert_1m_contract(row, start=1000):
    assert int(row[0]) == start
    assert int(row[6]) == start + 60_000
    assert float(row[2]) >= max(float(row[1]), float(row[4]))
    assert float(row[3]) <= min(float(row[1]), float(row[4]))


def test_candle_contract_repairs_open_time_as_close_time():
    row = [1000, 1, 3, 0.5, 2, 10, 1000, 20, 0, 0, 0, "0"]
    out = normalize_existing_rows([row], "1m")
    _assert_1m_contract(out[0])


def test_bybit_1m_mapping_and_close_time(monkeypatch):
    from bybit_futures_client import BybitFuturesClient
    c = BybitFuturesClient()
    seen = {}
    def fake_get(path, params=None, use_cache=True):
        seen.update(params or {})
        return {"list": [["1000", "1", "3", "0.5", "2", "10", "20"]]}
    monkeypatch.setattr(c, "_get", fake_get)
    row = c.klines("BTCUSDT", "1m", 1)[0]
    assert seen["interval"] == "1"
    _assert_1m_contract(row)


def test_bitget_1m_contract(monkeypatch):
    from bitget_futures_client import BitgetFuturesClient
    c = BitgetFuturesClient()
    seen = {}
    def fake_get(path, params=None):
        seen.update(params or {})
        return [["1000", "1", "3", "0.5", "2", "10", "20"]]
    monkeypatch.setattr(c, "_get", fake_get)
    row = c.klines("BTCUSDT", "1m", 1)[0]
    assert seen["granularity"] == "1m"
    _assert_1m_contract(row)


def test_gate_1m_contract(monkeypatch):
    from gate_futures_client import GateFuturesClient
    c = GateFuturesClient()
    monkeypatch.setattr(c, "_contract", lambda symbol: "BTC_USDT")
    seen = {}
    def fake_get(path, params=None):
        seen.update(params or {})
        return [{"t": 1, "o": "1", "h": "3", "l": "0.5", "c": "2", "v": "10", "sum": "20"}]
    monkeypatch.setattr(c, "_get", fake_get)
    row = c.klines("BTCUSDT", "1m", 1)[0]
    assert seen["interval"] == "1m"
    _assert_1m_contract(row)


def test_htx_1m_mapping_and_contract(monkeypatch):
    from htx_futures_client import HtxFuturesClient
    c = HtxFuturesClient()
    seen = {}
    def fake_get(path, params=None):
        seen.update(params or {})
        return [{"id": 1, "open": "1", "high": "3", "low": "0.5", "close": "2", "amount": "10", "trade_turnover": "20", "count": 1}]
    monkeypatch.setattr(c, "_get", fake_get)
    row = c.klines("BTCUSDT", "1m", 1)[0]
    assert seen["period"] == "1min"
    _assert_1m_contract(row)


def test_kucoin_1m_mapping_and_contract(monkeypatch):
    from kucoin_futures_client import KucoinFuturesClient
    c = KucoinFuturesClient()
    monkeypatch.setattr(c, "_contract", lambda symbol: "XBTUSDTM")
    seen = {}
    def fake_get(path, params=None):
        seen.update(params or {})
        return [[1000, "1", "3", "0.5", "2", "10", "20"]]
    monkeypatch.setattr(c, "_get", fake_get)
    row = c.klines("BTCUSDT", "1m", 1)[0]
    assert seen["granularity"] == 60
    _assert_1m_contract(row)


def test_mexc_futures_1m_mapping_and_contract(monkeypatch):
    from mexc_futures_client import MexcFuturesClient
    c = MexcFuturesClient()
    seen = {}
    def fake_get(path, params=None):
        seen.update(params or {})
        return {"time": [1], "open": ["1"], "high": ["3"], "low": ["0.5"], "close": ["2"], "vol": ["10"], "amount": ["20"]}
    monkeypatch.setattr(c, "_get", fake_get)
    row = c.klines("BTCUSDT", "1m", 1)[0]
    assert seen["interval"] == "Min1"
    _assert_1m_contract(row)


def test_okx_1m_contract(monkeypatch):
    from okx_futures_client import OkxFuturesClient
    c = OkxFuturesClient()
    seen = {}
    def fake_get(path, params=None):
        seen.update(params or {})
        return [["1000", "1", "3", "0.5", "2", "10", "0", "20"]]
    monkeypatch.setattr(c, "_get", fake_get)
    row = c.klines("BTCUSDT", "1m", 1)[0]
    assert seen["bar"] == "1m"
    _assert_1m_contract(row)


def test_bingx_1m_contract(monkeypatch):
    from bingx_futures_client import BingxFuturesClient
    c = BingxFuturesClient()
    seen = {}
    def fake_get(path, params=None):
        seen.update(params or {})
        return [{"time": 1000, "open": "1", "high": "3", "low": "0.5", "close": "2", "volume": "10", "quoteVolume": "20"}]
    monkeypatch.setattr(c, "_get", fake_get)
    row = c.klines("BTCUSDT", "1m", 1)[0]
    assert seen["interval"] == "1m"
    _assert_1m_contract(row)


def test_hyperliquid_1m_contract(monkeypatch):
    from hyperliquid_futures_client import HyperliquidFuturesClient
    c = HyperliquidFuturesClient()
    monkeypatch.setattr(c, "_coin", lambda symbol: "BTC")
    monkeypatch.setattr(c, "_post", lambda payload: [{"t": 1000, "T": 1000, "o": "1", "h": "3", "l": "0.5", "c": "2", "v": "10", "n": 1}])
    row = c.klines("BTCUSDT", "1m", 1)[0]
    _assert_1m_contract(row)


def test_fallback_client_enforces_candle_contract(monkeypatch):
    import trade_market_client as tmc
    class Venue:
        def klines(self, symbol, interval, limit):
            return [[1000, 1, 3, 0.5, 2, 10, 1000, 20, 0, 0, 0, "0"]]
    monkeypatch.setattr(tmc, "_available", lambda *a, **k: True)
    monkeypatch.setattr(tmc, "_provider_supports_symbol", lambda *a, **k: True)
    monkeypatch.setattr(tmc, "_rate_limit", lambda *a, **k: None)
    monkeypatch.setattr(tmc, "_mark_success", lambda *a, **k: None)
    c = tmc.FallbackTradeMarketClient(providers=[])
    c.provider_names = ["fake"]
    c.clients = {"fake": Venue()}
    row = c.klines("BTCUSDT", "1m", 1)[0]
    _assert_1m_contract(row)


def test_mexc_spot_all_429_raises_runtimeerror_not_unboundlocal(monkeypatch):
    import mexc_client as m
    class Resp:
        status_code = 429
        def raise_for_status(self): pass
    class Session:
        def get(self, *a, **k): return Resp()
        def close(self): pass
    c = m.MexcSpotClient()
    c.session = Session()
    monkeypatch.setattr(c, "_read_cache", lambda *a, **k: None)
    monkeypatch.setattr(m.time, "sleep", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="rate limited"):
        c._get("/api/v3/ping", use_cache=False)


def test_paper_pending_earlier_ambiguous_blocks_later_fill():
    import paper_trading as p
    base = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    candles = [
        (base, 105, 106, 99, 104, True, False),
        (base + timedelta(minutes=1), 104, 105, 99, 100, False, False),
    ]
    touched, when, uncertain = p._scan_pending_entry_candles(candles, target=100, side="LONG", setup="PULLBACK", interval_minutes=1)
    assert touched is False and when is None and uncertain is True


def test_paper_earlier_ambiguous_stop_blocks_later_tp():
    import paper_trading as p
    base = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    candles = [
        (base, 100, 103, 94, 101, True, False),
        (base + timedelta(minutes=1), 101, 111, 100, 110, False, False),
    ]
    reason, price, when, uncertain = p._scan_open_exit_candles(candles, side="LONG", stop=95, tp=110, liquidation=80)
    assert reason is None and price is None and when is None and uncertain is True


def test_paper_safe_boundary_without_any_event_allows_later_tp():
    import paper_trading as p
    base = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    candles = [
        (base, 100, 104, 98, 101, True, False),
        (base + timedelta(minutes=1), 101, 111, 100, 110, False, False),
    ]
    reason, price, when, uncertain = p._scan_open_exit_candles(candles, side="LONG", stop=95, tp=110, liquidation=80)
    assert (reason, price, uncertain) == ("TP1", 110, False)


def test_paper_same_full_candle_tp_and_sl_is_conservative_loss():
    import paper_trading as p
    base = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    candles = [(base, 100, 111, 94, 101, False, False)]
    reason, price, _, uncertain = p._scan_open_exit_candles(candles, side="LONG", stop=95, tp=110, liquidation=80)
    assert (reason, price, uncertain) == ("SL_CONSERVATIVE", 95, False)


def test_shadow_source_has_chronology_barrier_for_touched_boundary():
    from pathlib import Path
    src = Path("shadow_signals.py").read_text()
    start = src.index("if boundary:")
    block = src[start:start + 500]
    assert "boundary_uncertain=True" in block
    assert "break" in block
