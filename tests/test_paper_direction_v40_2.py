import paper_trading as pt


def test_paper_accepts_canonical_scanner_bias_directions():
    assert pt._side("LONG_BIAS") == "LONG"
    assert pt._side("SHORT_BIAS") == "SHORT"
    assert pt._side("LONG BIAS") == "LONG"
    assert pt._side("SHORT-BIAS") == "SHORT"
    assert pt._side("NO_TRADE") is None


def test_main_scanner_signal_reaches_pending_paper(monkeypatch):
    signal = {
        "fingerprint": "scanner-long-bias",
        "symbol": "BTCUSDT",
        "direction": "LONG_BIAS",
        "setup": "PULLBACK",
        "entryPrice": 100.0,
        "entryText": "99.5–100.5",
        "stop": 95.0,
        "tp1": 110.0,
        "qualityScore": 80.0,
        "calibratedProbability": 75.0,
        "expectedValuePct": 2.5,
    }
    monkeypatch.setattr(pt, "_bool", lambda name, default: True if name == "PAPER_TRADING_ENABLED" else default)
    monkeypatch.setattr(pt.paper_repo, "position_by_fingerprint", lambda fp: None)
    created = {}
    def create(row, **kwargs):
        created.update(row)
        return {"id": "p1", **row}
    monkeypatch.setattr(pt.paper_repo, "create_pending_atomic", create)
    class Market:
        last_provider = "binance"
        def ticker_24h(self, symbol):
            return {}
    monkeypatch.setattr(pt, "create_trade_market_client", lambda *a, **k: Market())
    out = pt.open_from_signal(signal, source="automatic_monitor")
    assert out["status"] == "pending_entry"
    assert created["side"] == "LONG"
    assert created["symbol"] == "BTCUSDT"
