from __future__ import annotations
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _sample(i):
    return {
        'fingerprint': f'f{i}', 'symbol': 'BTCUSDT', 'timeframe': '1h', 'direction': 'LONG',
        'created_at': (datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(hours=i)).isoformat(),
        'old_score': 60.0, 'factors': {
            'trend':60,'momentum':60,'volume':60,'funding':50,'open_interest':60,'alignment':60,
            'risk_reward':60,'capital_flow':60,'narrative':50,'news':50,'smart_money':60,
        }, 'returns': {'24h': 1.0}, 'return': 1.0, 'win': 1.0, 'regime': 'range'
    }


def test_v14_holdout_is_never_seen_by_optimizer(monkeypatch, tmp_path):
    import learning_engine_v14 as le
    samples=[_sample(i) for i in range(220)]
    seen=[]
    monkeypatch.setattr(le, 'DB_PATH', tmp_path/'learn.db')
    monkeypatch.setattr(le, '_RESTORE_ATTEMPTED', True)
    monkeypatch.setattr(le, 'load_samples', lambda: samples)
    monkeypatch.setattr(le, 'active_model', lambda defaults: {'version':'old','weights':dict(defaults),'config':{},'metrics':{},'rules':[]})
    def opt(rows, defaults, seed):
        seen.append([r['fingerprint'] for r in rows]); return dict(defaults)
    monkeypatch.setattr(le, 'optimize_weights', opt)
    monkeypatch.setattr(le, 'evaluate', lambda rows, weights: {'utility':0,'brier':1,'top_avg_return':0,'rank_corr':0,'samples':len(rows)})
    monkeypatch.setattr(le, '_candidate_better', lambda *a, **k: False)
    monkeypatch.setattr(le, '_derive_rules', lambda rows: [])
    monkeypatch.setattr(le, '_calibration', lambda rows, weights: [])
    monkeypatch.setattr(le, '_drift', lambda rows: {'score':0,'status':'stable','details':{}})
    result=le.train(le.DEFAULT_WEIGHTS if hasattr(le,'DEFAULT_WEIGHTS') else {k:1.0 for k in le.FEATURES})
    holdout=set(r['fingerprint'] for r in samples[-result['samples_validation']:])
    assert seen
    assert all(not (set(call) & holdout) for call in seen)


def test_paper_cursor_advances_only_to_returned_market_history(monkeypatch):
    import paper_trading as pt
    now=datetime(2026,8,24,12,5,tzinfo=timezone.utc)
    position={
        'id':'p1','fingerprint':'f1','symbol':'BTCUSDT','side':'LONG','status':'open',
        'entry_price':100.0,'stop_price':90.0,'tp1_price':110.0,'estimated_liquidation_price':80.0,
        'opened_at':'2026-08-24T12:00:00+00:00','last_checked_at':'2026-08-24T12:00:00+00:00',
        'max_hold_until':'2026-08-24T13:00:00+00:00','execution_provider':'',
    }
    rows=[]
    for minute in (0,1):
        start=datetime(2026,8,24,12,minute,tzinfo=timezone.utc)
        end=start+timedelta(minutes=1)
        rows.append([int(start.timestamp()*1000),'100','101','99','100','0',int(end.timestamp()*1000)])
    monkeypatch.setattr(pt, '_now', lambda: now)
    monkeypatch.setattr(pt, '_pending_positions', lambda: [])
    monkeypatch.setattr(pt, '_open_positions', lambda: [position])
    monkeypatch.setattr(pt, 'create_trade_market_client', lambda *a, **k: object())
    monkeypatch.setattr(pt, '_execution_klines', lambda *a, **k: (rows,'1m',1))
    monkeypatch.setattr(pt, '_current_market_price', lambda *a, **k: 100.0)
    updates=[]
    monkeypatch.setattr(pt.paper_repo, 'update_position', lambda pid, values, **kw: updates.append(values) or {'id':pid})
    pt.update_positions()
    cursor=[u['last_checked_at'] for u in updates if 'last_checked_at' in u][-1]
    assert cursor.startswith('2026-08-24T12:02:00')


def test_strategy_entry_candle_cannot_resolve_outcome(monkeypatch):
    import strategies.service as svc
    created=datetime.now(timezone.utc)-timedelta(hours=3)
    setup={'id':'s1','symbol':'BTCUSDT','created_at':created.isoformat(),'state':'waiting_entry','entry_price':100.0,
           'stop_price':95.0,'tp_price':110.0,'direction':'LONG','payload':{'entry_mode':'LIMIT'}}
    start=created+timedelta(hours=1)
    # Same candle touches entry and TP; ordering is unknowable.
    rows=[[int(start.timestamp()*1000),105,111,99,106,1000]]
    class C:
        def klines(self,*a,**k): return rows
    monkeypatch.setattr(svc.repository,'active_setups',lambda strategy, limit:[setup])
    updates=[]
    monkeypatch.setattr(svc.repository,'update_setup',lambda sid, values: updates.append(values))
    monkeypatch.setattr(svc,'create_trade_market_client',lambda:C())
    monkeypatch.setattr(svc,'stats',lambda *a,**k:{})
    out=svc.update_outcomes(strategy='fib_05_pullback')
    assert out['won']==0 and out['lost']==0
    assert updates and updates[0]['state']=='open'


def test_adaptive_candidate_cannot_replace_better_champion(monkeypatch):
    import adaptive_model_manager as am
    rows=[{'signal_payload':{},'net_pnl':1,'closed_at':str(i)} for i in range(60)]
    monkeypatch.setattr(am,'_load_rows',lambda limit:rows)
    monkeypatch.setattr(am,'_extract',lambda row:([50.0]*len(am.FEATURES),1))
    monkeypatch.setattr(am,'_standardize',lambda xs:(xs,[0.0]*len(am.FEATURES),[1.0]*len(am.FEATURES)))
    monkeypatch.setattr(am,'_train',lambda xs,ys:([0.0]*len(am.FEATURES),0.0))
    calls={'metrics':0,'inserted':None,'archived':False}
    def fake_metrics(rows, model):
        calls['metrics']+=1
        if calls['metrics']==1:
            return {'samples':len(rows),'accuracy':.7,'log_loss':.4,'brier':.2,'baseline_log_loss':.5,'baseline_brier':.25}
        return {'samples':len(rows),'accuracy':.8,'log_loss':.3,'brier':.15,'baseline_log_loss':.5,'baseline_brier':.25}
    monkeypatch.setattr(am,'_metrics',fake_metrics)
    class Resp:
        def __init__(self,data): self.data=data
    class Q:
        def __init__(self): self.mode='select'
        def select(self,*a,**k): return self
        def eq(self,*a,**k): return self
        def order(self,*a,**k): return self
        def limit(self,*a,**k): return self
        def update(self,*a,**k): calls['archived']=True; return self
        def insert(self,row): calls['inserted']=row; self.mode='insert'; return self
        def execute(self):
            if self.mode=='select': return Resp([{'version':'champ','model_json':{'means':[],'stds':[],'weights':[],'bias':0},'metrics':{}}])
            return Resp([])
    class Client:
        def table(self,name): return Q()
    monkeypatch.setattr(am,'_client',lambda:Client())
    out=am.train_candidate('manual')
    assert out['status']=='candidate'
    assert not calls['archived']
    assert calls['inserted']['status']=='candidate'


def test_trade_monitor_registers_paper_even_when_telegram_fails(monkeypatch):
    import trade_monitor as tm
    obj=object.__new__(tm.TradeMonitor)
    obj.logger=lambda text: None
    obj.sender=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('telegram down'))
    signal={'fingerprint':'fp','symbol':'BTCUSDT','direction':'LONG_BIAS','setup':'PULLBACK'}
    monkeypatch.setattr(tm,'upsert_watch_candidate',lambda *a,**k:None)
    monkeypatch.setattr(tm,'signal_recently_sent',lambda *a,**k:False)
    monkeypatch.setattr(tm,'save_signal',lambda *a,**k:1)
    monkeypatch.setattr(tm,'persist_trade_signal',lambda *a,**k:'cloud')
    monkeypatch.setattr(tm,'build_signal_block',lambda s:'signal')
    marked=[]
    monkeypatch.setattr(tm,'mark_signal_sent',lambda sid: marked.append(sid))
    import paper_trading as pt
    opened=[]
    monkeypatch.setattr(pt,'open_from_signal',lambda s,source='signal': opened.append(s['fingerprint']) or {'status':'pending_entry','position':{}})
    monkeypatch.setattr(pt,'format_pending_message',lambda r:'pending')
    obj._process_signals({'signals':[signal]},42,'monitor')
    assert opened==['fp']
    assert marked==[]


def test_cloud_rows_query_newest_limit_then_return_chronological(monkeypatch):
    import sys, types
    fake_client=types.ModuleType('cloud_client'); fake_client.get_supabase_client=lambda: None
    monkeypatch.setitem(sys.modules,'cloud_client',fake_client)
    sys.modules.pop('cloud_learning_store', None)
    from cloud_learning_store import CloudLearningStore
    class Resp:
        data=[{'signal_created_at':'3'},{'signal_created_at':'2'},{'signal_created_at':'1'}]
    class Q:
        desc=None
        def select(self,*a): return self
        @property
        def not_(self): return self
        def is_(self,*a): return self
        def order(self,key,desc=False): self.desc=desc; return self
        def limit(self,n): return self
        def execute(self): assert self.desc is True; return Resp()
    store=object.__new__(CloudLearningStore); store.client=type('C',(),{'table':lambda self,n:Q()})()
    rows=store.resolved_rows(3)
    assert [r['signal_created_at'] for r in rows]==['1','2','3']


def test_learning_max_preserves_explicit_regime_with_empty_features(monkeypatch, tmp_path):
    import learning_max2 as lm
    monkeypatch.setattr(lm,'DB_PATH',tmp_path/'lm.db')
    lm.save_observation({'fingerprint':'f','symbol':'BTCUSDT','marketRegime':'bull_trend','aiFactors':{}})
    with lm.connect() as conn:
        row=conn.execute("select market_regime from feature_store where fingerprint='f'").fetchone()
    assert row['market_regime']=='bull_trend'


def test_profit_profile_atomic_writer(monkeypatch, tmp_path):
    import build_profit_profile as bp
    out=tmp_path/'profile.json'
    bp._atomic_write_json(out, {'ok':True})
    assert out.exists() and 'true' in out.read_text().lower()
    assert not (tmp_path/'profile.json.tmp').exists()


def test_paper_does_not_use_live_ticker_while_history_is_stale(monkeypatch):
    import paper_trading as pt
    now=datetime(2026,8,24,12,10,tzinfo=timezone.utc)
    position={
        'id':'p2','fingerprint':'f2','symbol':'BTCUSDT','side':'LONG','status':'open',
        'entry_price':100.0,'stop_price':95.0,'tp1_price':110.0,'estimated_liquidation_price':80.0,
        'opened_at':'2026-08-24T12:00:00+00:00','last_checked_at':'2026-08-24T12:00:00+00:00',
        'max_hold_until':'2026-08-24T13:00:00+00:00','execution_provider':'',
    }
    start=datetime(2026,8,24,12,0,tzinfo=timezone.utc)
    rows=[[int(start.timestamp()*1000),'100','101','99','100','0',int((start+timedelta(minutes=1)).timestamp()*1000)]]
    monkeypatch.setattr(pt,'_now',lambda:now)
    monkeypatch.setattr(pt,'_pending_positions',lambda:[])
    monkeypatch.setattr(pt,'_open_positions',lambda:[position])
    monkeypatch.setattr(pt,'create_trade_market_client',lambda *a,**k:object())
    monkeypatch.setattr(pt,'_execution_klines',lambda *a,**k:(rows,'1m',1))
    ticker=[]
    monkeypatch.setattr(pt,'_current_market_price',lambda *a,**k:ticker.append(True) or 120.0)
    monkeypatch.setattr(pt.paper_repo,'update_position',lambda *a,**k:{'id':'p2'})
    closed=[]
    monkeypatch.setattr(pt,'_close_position',lambda *a,**k:closed.append(True))
    pt.update_positions()
    assert ticker==[] and closed==[]


def test_strategy_ignores_current_unclosed_hour(monkeypatch):
    import strategies.service as svc
    now=datetime.now(timezone.utc)
    created=now-timedelta(hours=2)
    setup={'id':'s2','symbol':'BTCUSDT','created_at':created.isoformat(),'state':'waiting_entry','entry_price':100.0,
           'stop_price':95.0,'tp_price':110.0,'direction':'LONG','payload':{'entry_mode':'LIMIT'}}
    current_start=now.replace(minute=0,second=0,microsecond=0)
    rows=[[int(current_start.timestamp()*1000),105,111,99,106,1000]]
    class C:
        def klines(self,*a,**k): return rows
    monkeypatch.setattr(svc.repository,'active_setups',lambda strategy, limit:[setup])
    updates=[]
    monkeypatch.setattr(svc.repository,'update_setup',lambda sid, values:updates.append(values))
    monkeypatch.setattr(svc,'create_trade_market_client',lambda:C())
    monkeypatch.setattr(svc,'stats',lambda *a,**k:{})
    out=svc.update_outcomes(strategy='fib_05_pullback')
    assert out['opened']==0 and updates==[]
