from strategies.catalog import get_strategy
from strategies.analyzers import analyze_strategy, ma55_cycle_event, recent_ma55_cycle_event
from strategies.service import stats
from strategies.reports import strategy_notification_messages


def _rows(closes, ms=14_400_000, volume=2_000_000.0):
    out=[]
    for i,c in enumerate(closes):
        o=closes[i-1] if i else c
        out.append([1_700_000_000_000+i*ms,o,max(o,c)*1.002,min(o,c)*0.998,c,volume*(1.2 if i>=len(closes)-3 else 1.0)])
    return out


def test_ma55_cycle_registered():
    spec=get_strategy('ma55cycle')
    assert spec.key=='ma55_cycle'
    assert '8/13/21/55' in spec.title


def test_ma55_cycle_analyzer_has_custom_exit_mode():
    d1=_rows([100+i*0.5 for i in range(260)], ms=86_400_000)
    # decline keeps slow MA above fasts, then recovery moves fast ribbon above slow.
    h4=_rows([130-i*0.18 for i in range(75)] + [116,115,114,113,114,116,119,123,127,131,135,139,143,147,151,155,159,163])
    r=analyze_strategy('ma55_cycle','TESTUSDT',250_000_000,d1,h4,'test',{})
    assert r['status'] in {'READY','WATCH','WAITING','NO_SETUP'}
    if r.get('entry_price'):
        assert r['entry_mode']=='NEXT_BAR_MARKET'
        assert r['exit_mode']=='MA55_CROSS_UP_ALL'
        assert r['stop_price'] < r['entry_price']


def test_notification_messages_buy_and_close():
    payload={'runs':[{'strategy':'ma55_cycle','new_ready_events':[{
        'symbol':'BTCUSDT','reference_price':100,'stop_price':95,'score':88,'reason':'cross'
    }], 'outcome_events':[{
        'symbol':'BTCUSDT','outcome':'MA55_REVERSE_CROSS','entry_price':100,'exit_price':112,'return_pct':12
    }]}]}
    msgs=strategy_notification_messages(payload)
    assert len(msgs)==2
    assert 'BUY SIGNAL' in msgs[0]
    assert 'CLOSE LONG' in msgs[1]


def test_scheduler_prioritizes_ma55_cycle(monkeypatch):
    import strategies.scheduler as scheduler
    monkeypatch.setattr(scheduler, 'is_trade_scan_running', lambda: False)
    monkeypatch.setattr(scheduler, 'is_strategy_scan_running', lambda: False)
    # choose a non-ma55 strategy as the normal round-robin item
    other=next(x for x in scheduler.STRATEGIES if x.key!='ma55_cycle')
    monkeypatch.setattr(scheduler, '_next_spec', lambda: other)
    calls=[]
    monkeypatch.setattr(scheduler, 'run_strategy_scan', lambda key, force_parallel_budget=False: {'summary':{'strategy':key,'analyzed':1,'ready':0,'watch':0},'results':[]})
    monkeypatch.setattr(scheduler, 'boolean', lambda name, default=True: True)
    result=scheduler.run_scheduled_cycle()
    keys=[x['strategy'] for x in result['runs']]
    assert keys[0]=='ma55_cycle'
    assert other.key in keys


def test_ma55_cycle_buy_event_can_complete_sequentially():
    closes=[130-i*.2 for i in range(70)] + [116,118,121,125,130,136,143,151]
    event=ma55_cycle_event(_rows(closes), 'BUY', 12)
    assert event is not None
    assert event['type']=='BUY'
    assert event['bars'] <= 12


def test_ma55_cycle_reverse_exit_event():
    closes=[130-i*.2 for i in range(70)] + [116,118,121,125,130,136,143,151,160,170,181,193,206,220]
    closes += [220-i*5 for i in range(1,35)]
    found=None
    for end in range(60, len(closes)+1):
        event=ma55_cycle_event(_rows(closes[:end]), 'EXIT', 12)
        if event:
            found=event
            break
    assert found is not None
    assert found['type']=='EXIT'


def test_ma55_cycle_cross_remains_active_for_three_h4_bars():
    closes=[130-i*.2 for i in range(70)] + [116,118,121,125,130,136,143,151]
    base=_rows(closes)
    fresh=ma55_cycle_event(base, 'BUY', 12)
    assert fresh is not None
    # Add two later bars: the original one-shot detector no longer emits, but
    # the active-window detector must still return the original cross.
    later=_rows(closes + [154,157])
    assert ma55_cycle_event(later, 'BUY', 12) is None
    active=recent_ma55_cycle_event(later, 'BUY', active_bars=3, transition_lookback_bars=12)
    assert active is not None
    assert active['age_bars'] == 2
    assert active['ts'] == fresh['ts']


def test_ma55_cycle_cross_expires_after_three_h4_bars():
    closes=[130-i*.2 for i in range(70)] + [116,118,121,125,130,136,143,151]
    later=_rows(closes + [154,157,160,163])
    active=recent_ma55_cycle_event(later, 'BUY', active_bars=3, transition_lookback_bars=12)
    assert active is None


def test_ma55_helpers_accept_normalized_dict_candles_regression():
    from strategies.fib_pullback import normalize_klines
    closes=[130-i*.2 for i in range(70)] + [116,118,121,125,130,136,143,151]
    normalized=normalize_klines(_rows(closes))
    event=ma55_cycle_event(normalized, 'BUY', 12)
    assert event is not None
    assert event['type'] == 'BUY'
