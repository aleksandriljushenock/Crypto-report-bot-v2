from __future__ import annotations

import types


def test_provider_registry_contains_all_ten():
    from exchanges.registry import supported_names
    assert supported_names() == (
        'binance','bybit','okx','bitget','gate','mexc','bingx','kucoin','hyperliquid','htx'
    )


def test_market_capability_unavailable_is_not_zero():
    from exchanges.capabilities import CapabilityValue
    from main import _market_call
    class Client:
        def capability(self, method, *args, **kwargs):
            return CapabilityValue('unavailable', provider='x', error='no data')
    value = _market_call(Client(), 'open_interest', 'BTCUSDT')
    assert value['capability'] == 'unavailable'
    assert value['error'] == 'no data'
    assert value != 0


def test_strategy_specs_cover_execution_and_chronos():
    from strategy_settings import SPEC_BY_KEY
    for key in (
        'PAPER_ENTRY_MAX_WAIT_HOURS','PAPER_MAX_ENTRY_DEVIATION_PCT',
        'PAPER_ENTRY_SLIPPAGE_PCT','NEAR_SIGNAL_TTL_HOURS',
        'SHADOW_SIGNAL_TTL_HOURS','CHRONOS_ENABLED','CHRONOS_FINALISTS',
    ):
        assert key in SPEC_BY_KEY


def test_fill_compare_and_set_prevents_double_debit(monkeypatch):
    import paper_trading as pt
    position = {
        'id':'p1','fingerprint':'f1','symbol':'BTCUSDT','side':'LONG',
        'stop_price':90.0,'tp1_price':120.0,'signal_payload':{'suggestedPositionSizeUsd':4.0},
        'execution_audit':{},
    }
    account = {'balance':100.0,'equity':100.0,'fees_paid':0.0}
    monkeypatch.setattr(pt, 'ensure_account', lambda: dict(account))
    monkeypatch.setattr(pt.paper_repo, 'fill_pending_atomic', lambda **k: None)
    calls=[]
    monkeypatch.setattr(pt.paper_repo, 'update_account', lambda *a, **k: calls.append((a,k)))
    out=pt._fill_pending_position(position, 100.0, 'test')
    assert out['status']=='already-processed-or-insufficient-balance'
    assert calls == []


def test_close_compare_and_set_prevents_duplicate_trade(monkeypatch):
    import paper_trading as pt
    position = {
        'id':'p1','fingerprint':'f1','symbol':'BTCUSDT','side':'LONG',
        'entry_price':100.0,'notional_usd':40.0,'margin_usd':4.0,'entry_fee':0.024,
        'stop_price':90.0,'tp1_price':120.0,'leverage':10,'opened_at':'2026-01-01T00:00:00+00:00',
    }
    monkeypatch.setattr(pt, 'ensure_account', lambda: {'balance':96.0,'equity':99.976,'realized_pnl':0,'fees_paid':0.024})
    monkeypatch.setattr(pt.paper_repo, 'update_position', lambda *a, **k: None)
    trades=[]; accounts=[]
    monkeypatch.setattr(pt.paper_repo, 'upsert_trade', lambda row: trades.append(row))
    monkeypatch.setattr(pt.paper_repo, 'update_account', lambda *a, **k: accounts.append((a,k)))
    assert pt._close_position(position, 110.0, 'TP1') == {}
    assert trades == [] and accounts == []


def test_close_equity_preserves_other_reserved_positions(monkeypatch):
    import paper_trading as pt
    position = {
        'id':'p1','fingerprint':'f1','symbol':'BTCUSDT','side':'LONG',
        'entry_price':100.0,'notional_usd':40.0,'margin_usd':4.0,'entry_fee':0.0,
        'stop_price':90.0,'tp1_price':120.0,'leverage':10,'opened_at':'2026-01-01T00:00:00+00:00',
    }
    account={'balance':90.0,'equity':100.0,'realized_pnl':0.0,'fees_paid':0.0}
    monkeypatch.setattr(pt, 'ensure_account', lambda: dict(account))
    monkeypatch.setattr(pt.paper_repo, 'update_position', lambda *a, **k: {'id':'p1'})
    monkeypatch.setattr(pt.paper_repo, 'upsert_trade', lambda row: None)
    updates=[]
    monkeypatch.setattr(pt.paper_repo, 'update_account', lambda aid, values: updates.append(values))
    monkeypatch.setattr(pt, '_float', lambda name, default: 0.0 if name in {'PAPER_FEE_PCT_PER_SIDE','PAPER_SLIPPAGE_PCT'} else default)
    out=pt._close_position(position, 110.0, 'TP1')
    assert out
    # +4 gross pnl on a $40 notional after +10% move. Equity must be 104,
    # not free balance 98 (which would ignore other reserved margin).
    assert abs(updates[-1]['equity'] - 104.0) < 1e-9
    assert abs(updates[-1]['balance'] - 98.0) < 1e-9

def test_runtime_config_prefers_strategy_setting(monkeypatch):
    import core.runtime_config as rc
    import strategy_settings
    monkeypatch.setattr(strategy_settings, 'current_value', lambda key: 123 if key == 'TRADE_TOP_LIQUID_SYMBOLS' else None)
    assert rc.integer('TRADE_TOP_LIQUID_SYMBOLS', 80) == 123
