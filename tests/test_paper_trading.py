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


def _open_position(**overrides):
    row = {
        'id':'p1','fingerprint':'f1','symbol':'BTCUSDT','side':'LONG','status':'open',
        'entry_price':100.0,'stop_price':95.0,'tp1_price':110.0,
        'estimated_liquidation_price':90.0,'notional_usd':100.0,'margin_usd':10.0,
        'entry_fee':0.06,'leverage':10,'opened_at':'2026-08-13T12:00:30+00:00',
        'last_checked_at':'2026-08-13T12:03:00+00:00','max_hold_until':'2099-01-01T00:00:00+00:00',
        'execution_audit':{},
    }
    row.update(overrides)
    return row


def test_execution_window_keeps_current_candle_after_last_checked():
    from datetime import datetime, timezone
    import paper_trading as pt
    since = datetime(2026, 8, 13, 12, 3, tzinfo=timezone.utc)
    # Candle starts before last_checked_at but ends after it. Old logic dropped it.
    rows = [[
        int(datetime(2026,8,13,12,0,tzinfo=timezone.utc).timestamp()*1000),
        '100','101','89','92','0',
        int(datetime(2026,8,13,12,4,59,tzinfo=timezone.utc).timestamp()*1000),
    ]]
    candles = pt._iter_execution_candles(rows, since=since, interval_minutes=5)
    assert len(candles) == 1
    assert candles[0][3] == 89.0


def test_update_positions_ignores_pre_open_candle_extremes(monkeypatch):
    import paper_trading as pt
    position = _open_position()
    monkeypatch.setattr(pt, '_pending_positions', lambda: [])
    monkeypatch.setattr(pt, '_open_positions', lambda: [position])
    monkeypatch.setattr(pt, 'create_trade_market_client', lambda: object())
    monkeypatch.setattr(pt, '_current_market_price', lambda client, symbol: 100.0)
    from datetime import datetime, timezone
    rows = [[int(datetime(2026,8,13,12,0,tzinfo=timezone.utc).timestamp()*1000),'100','101','89','92','0',int(datetime(2026,8,13,12,4,59,tzinfo=timezone.utc).timestamp()*1000)]]
    monkeypatch.setattr(pt, '_execution_klines', lambda client, symbol: (rows, '5m', 5))
    closed=[]
    monkeypatch.setattr(pt, '_close_position', lambda pos, price, reason, when=None: closed.append((price,reason)) or {'close_reason':reason,'net_pnl':-10})
    monkeypatch.setattr(pt.paper_repo, 'update_position', lambda *a, **k: {'id':'p1'})
    out = pt.update_positions()
    assert out['liquidated'] == 0
    assert closed == []


def test_live_price_beyond_liquidation_heals_stale_position(monkeypatch):
    import paper_trading as pt
    position = _open_position()
    monkeypatch.setattr(pt, '_pending_positions', lambda: [])
    monkeypatch.setattr(pt, '_open_positions', lambda: [position])
    monkeypatch.setattr(pt, 'create_trade_market_client', lambda: object())
    monkeypatch.setattr(pt, '_current_market_price', lambda client, symbol: 89.0)
    monkeypatch.setattr(pt, '_execution_klines', lambda client, symbol: ([], '1m', 1))
    closed=[]
    monkeypatch.setattr(pt, '_close_position', lambda pos, price, reason, when=None: closed.append((price,reason)) or {'close_reason':reason,'net_pnl':-10})
    out=pt.update_positions()
    assert out['liquidated'] == 1
    assert closed == [(90.0, 'LIQUIDATION')]


def test_liquidation_loss_is_capped_to_isolated_margin(monkeypatch):
    import paper_trading as pt
    position = _open_position(entry_fee=0.06)
    account={'balance':89.94,'equity':99.94,'realized_pnl':0.0,'fees_paid':0.06}
    monkeypatch.setattr(pt, 'ensure_account', lambda: dict(account))
    updates=[]
    monkeypatch.setattr(pt.paper_repo, 'update_position', lambda *a, **k: {'id':'p1'})
    monkeypatch.setattr(pt.paper_repo, 'upsert_trade', lambda row: None)
    monkeypatch.setattr(pt.paper_repo, 'update_account', lambda aid, values: updates.append(values))
    out=pt._close_position(position, 80.0, 'LIQUIDATION')
    assert out['gross_pnl'] == -10.0
    assert abs(out['net_pnl'] - (-10.06)) < 1e-9
    assert out['balance_after'] == 89.94
    assert abs(updates[-1]['equity'] - 89.94) < 1e-9


def test_performance_separates_breakeven_and_uses_ledger_balance(monkeypatch):
    import paper_trading as pt
    trades=[
        {'net_pnl':2.0,'close_reason':'TP1'},
        {'net_pnl':-1.0,'close_reason':'SL'},
        {'net_pnl':0.0,'close_reason':'TIME_EXIT'},
        {'net_pnl':-3.0,'close_reason':'LIQUIDATION'},
    ]
    monkeypatch.setattr(pt, 'ensure_account', lambda: {'initial_balance':100.0,'balance':1.0,'equity':2.0})
    monkeypatch.setattr(pt, 'get_recent_trades', lambda limit=20: trades)
    monkeypatch.setattr(pt, '_open_positions', lambda: [{'margin_usd':5.0,'entry_fee':0.1}])
    monkeypatch.setattr(pt, '_pending_positions', lambda: [])
    out=pt.performance()
    assert out['wins'] == 1 and out['losses'] == 2 and out['breakeven'] == 1
    assert out['liquidations'] == 1
    assert out['net_pnl'] == -2.0
    assert abs(out['derived_equity'] - 97.9) < 1e-9
    assert abs(out['derived_free_balance'] - 92.9) < 1e-9
