import os

from paper_trading import _leverage_and_liquidation, _signed_return


def test_long_liquidation_is_beyond_stop(monkeypatch):
    monkeypatch.setenv("PAPER_MAX_LEVERAGE", "20")
    monkeypatch.setenv("PAPER_LIQUIDATION_BUFFER_PCT", "0.5")
    monkeypatch.setenv("PAPER_MAINTENANCE_MARGIN_PCT", "0.5")
    leverage, liquidation, stop_distance, buffer_pct = _leverage_and_liquidation(100.0, 97.0, "LONG")
    assert 1 <= leverage <= 20
    assert liquidation < 97.0
    assert stop_distance == 3.0
    assert buffer_pct >= 0.5


def test_short_liquidation_is_beyond_stop(monkeypatch):
    monkeypatch.setenv("PAPER_MAX_LEVERAGE", "20")
    monkeypatch.setenv("PAPER_LIQUIDATION_BUFFER_PCT", "0.5")
    monkeypatch.setenv("PAPER_MAINTENANCE_MARGIN_PCT", "0.5")
    leverage, liquidation, _, buffer_pct = _leverage_and_liquidation(100.0, 103.0, "SHORT")
    assert 1 <= leverage <= 20
    assert liquidation > 103.0
    assert buffer_pct >= 0.5


def test_signed_return_direction():
    assert _signed_return("LONG", 100, 105) == 0.05
    assert _signed_return("SHORT", 100, 95) == 0.05
