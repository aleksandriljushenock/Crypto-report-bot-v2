from datetime import datetime, timedelta, timezone


def _ms(dt):
    return int(dt.timestamp() * 1000)


def test_pre_fill_candle_is_never_used_for_execution():
    import paper_trading as pt
    opened = datetime(2026, 8, 21, 12, 0, 30, tzinfo=timezone.utc)
    rows = [[_ms(datetime(2026,8,21,12,0,tzinfo=timezone.utc)), '100','120','80','100','0', _ms(datetime(2026,8,21,12,0,59,tzinfo=timezone.utc))]]
    assert pt._iter_execution_candles(rows, since=opened, interval_minutes=1, not_before=opened) == []


def test_expiry_boundary_candle_is_not_used():
    import paper_trading as pt
    created = datetime(2026,8,21,12,0,tzinfo=timezone.utc)
    expiry = created + timedelta(seconds=30)
    rows = [[_ms(created), '100','120','80','100','0', _ms(created + timedelta(seconds=59))]]
    assert pt._iter_execution_candles(rows, since=created-timedelta(seconds=1), interval_minutes=1, not_before=created, not_after=expiry) == []


def test_invalid_direction_is_rejected(monkeypatch):
    import paper_trading as pt
    monkeypatch.setenv('PAPER_TRADING_ENABLED','true')
    signal={'fingerprint':'x','symbol':'BTCUSDT','entryPrice':100,'stop':90,'tp1':120,'direction':'HOLD'}
    assert pt.open_from_signal(signal)['status'] == 'invalid-direction'


def test_optimizer_breakeven_not_counted_as_loss():
    import ai_optimizer as opt
    m=opt._metric([{'net_pnl':2},{'net_pnl':-1},{'net_pnl':0}])
    assert m['wins']==1 and m['losses']==1 and m['breakeven']==1
    assert m['win_rate']==50.0


def test_optimizer_never_shrinks_large_universe(monkeypatch):
    import ai_optimizer as opt
    class Q:
        data=[{'rows_analyzed':150,'signals_count':0,'created_at':'x'}]*10
        def select(self,*a): return self
        def order(self,*a,**k): return self
        def limit(self,*a): return self
        def execute(self): return self
    class C:
        def table(self,*a): return Q()
    monkeypatch.setattr(opt,'_client',lambda:C())
    monkeypatch.setattr(opt,'_int',lambda name, default: 150 if name=='TRADE_TOP_LIQUID_SYMBOLS' else (300 if name=='AI_OPTIMIZER_UNIVERSE_MAX' else default))
    r=opt._universe_recommendation()
    assert r and r['proposed_value'] > r['current_value'] == 150


def test_circuit_breaker_does_not_retry_blocked_provider(monkeypatch):
    import trade_market_client as tm
    class Bad:
        def ticker_24h(self,*a,**k): raise AssertionError('blocked provider called')
    c=tm.FallbackTradeMarketClient.__new__(tm.FallbackTradeMarketClient)
    c.provider_names=['binance']; c.clients={'binance':Bad()}; c.last_provider=None; c.last_errors=[]
    monkeypatch.setattr(tm,'_available',lambda name:False)
    try:
        c._call('ticker_24h','BTCUSDT')
    except RuntimeError as exc:
        assert 'cooldown' in str(exc)
    else:
        raise AssertionError('expected RuntimeError')
