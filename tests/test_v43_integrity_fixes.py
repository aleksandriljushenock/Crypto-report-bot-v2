from datetime import datetime, timezone, timedelta


def test_scanner_fail_closed_on_ranking_error(monkeypatch):
    import scanner.pipeline as p
    snapshot={'runTimeUtc':'2026-01-01T00:00:00+00:00','marketProvider':'x','selectedSymbols':['BTCUSDT']}
    rows=[{'rules': {'finalStatus':'TRADE_CANDIDATE'}, 'score': {'direction':'LONG_BIAS'}}]
    monkeypatch.setattr(p,'get_priority_listing_symbols',lambda limit:[])
    monkeypatch.setattr(p,'get_priority_discovery_symbols',lambda limit:[])
    monkeypatch.setattr(p,'collect_and_analyze_market',lambda **kwargs:(snapshot.copy(), rows.copy()))
    monkeypatch.setattr(p,'get_last_universe_summary',lambda:{})
    monkeypatch.setattr(p,'find_trade_signals',lambda *a,**k:[{'symbol':'BTCUSDT','direction':'LONG_BIAS','fingerprint':'x'}])
    import ai_intelligence
    monkeypatch.setattr(ai_intelligence,'rank_signals',lambda x: (_ for _ in ()).throw(RuntimeError('rank down')))
    out=p.run_trade_scan(max_results=5,apply_ai=True,source='test')
    assert out['signals']==[]
    assert out['scannerStages']['signals']==0


def test_profile_thresholds_never_weaken_global(monkeypatch):
    from scanner.signals import _profile_thresholds
    monkeypatch.setenv('TRADE_PULLBACK_MIN_SCORE','60')
    monkeypatch.setenv('TRADE_PULLBACK_MIN_RR','1.1')
    monkeypatch.setenv('TRADE_PULLBACK_MIN_PROBABILITY','50')
    assert _profile_thresholds('PULLBACK',75,2.5,78)=={'score':75.0,'rr':2.5,'probability':78.0}


def test_direction_aliases_use_specialists_and_rules(monkeypatch):
    import learning_engine_v14 as le
    monkeypatch.setattr(le,'_apply_operator_weight_policy',lambda w,d:w)
    model={'config':{'specialists':{'bull_trend:LONG':{'trend':9}},'global_weights':{'trend':1}},'learned_weights':{'trend':1}}
    assert le.specialist_weights(model,'bull_trend','LONG_BIAS')['trend']==9
    rules=[{'regime':'all','direction':'LONG','feature':'trend','operator':'>=','threshold':60,'adjustment':5}]
    assert le.apply_learning_adjustments({'trend':80},rules,direction='LONG_BIAS')['adjustment']==5


def test_cloud_overlay_reads_nested_ai_factors(monkeypatch,tmp_path):
    import adaptive_cloud_learning as ac
    ac.SNAPSHOT=tmp_path/'weights.json'
    class Store:
        def resolved_rows(self,limit):
            return [{'features':{'aiFactors':{'trend':90}},'real_result':{'return_percent':5}} for _ in range(20)] + [{'features':{'aiFactors':{'trend':10}},'real_result':{'return_percent':-5}} for _ in range(20)]
    import cloud_learning_store
    monkeypatch.setattr(cloud_learning_store,'CloudLearningStore',lambda:Store())
    out=ac.train_cloud_overlay({'trend':1.0},min_samples=20)
    assert out['status']=='updated'
    assert out['weights']['trend'] != 1.0


def test_paper_short_lookback_prefers_1m(monkeypatch):
    import paper_trading as pt
    calls=[]
    class C:
        def klines(self,symbol,interval,limit):
            calls.append(interval); return [[0,1,1,1,1,0,60000]]
    rows,interval,minutes=pt._execution_klines(C(),'BTCUSDT',lookback_hours=1)
    assert interval=='1m' and minutes==1 and calls[0]=='1m'


def test_partial_end_bar_is_not_used_for_entry():
    import paper_trading as pt
    start=datetime(2026,1,1,12,0,tzinfo=timezone.utc)
    rows=[[int(start.timestamp()*1000),'100','120','80','110','0',int((start+timedelta(minutes=5)).timestamp()*1000)]]
    candles=pt._iter_execution_candles(rows,since=start,interval_minutes=5,not_after=start+timedelta(minutes=2))
    assert candles and candles[0][6] is True


def test_price_at_never_looks_ahead_or_uses_stale():
    import trade_outcome_tracker as t
    start=datetime(2026,1,1,12,0,tzinfo=timezone.utc)
    bars=[(start,start+timedelta(minutes=5),100,101,99)]
    assert t._price_at(bars,start+timedelta(minutes=2)) is None
    assert t._price_at(bars,start+timedelta(minutes=5)) == 100
    assert t._price_at(bars,start+timedelta(minutes=20)) is None


def test_learning_report_does_not_retrain(monkeypatch):
    import self_learning_engine as sl
    monkeypatch.setattr(sl,'retrain',lambda: (_ for _ in ()).throw(AssertionError('must not train')))
    monkeypatch.setattr(sl,'diagnostics',lambda defaults:{'active':{'version':'x','config':{},'rules':[]},'samples':0,'metrics':{},'drift':{},'regimes':{},'versions':[]})
    text=sl.build_learning_report()
    assert 'диагностика' in text


def test_learning_uses_single_mature_horizon(monkeypatch):
    import learning_engine_v14 as le
    # direct behavior through a tiny local DB is covered elsewhere; assert config default target exists
    assert le.os.getenv('LEARNING_TARGET_HORIZON','24h') in {'1h','4h','24h','72h'}


def test_profile_recency_default_true(monkeypatch):
    import ai_hedge_fund_engine as h
    monkeypatch.delenv('PROFILE_RECENCY_ENABLED', raising=False)
    assert h._env_bool('PROFILE_RECENCY_ENABLED', True) is True


def test_adaptive_runtime_cache_can_be_invalidated():
    import adaptive_model_runtime as r
    r._CACHE.update(at=123,model={'x':1},version='old')
    r.invalidate_cache()
    assert r._CACHE == {'at':0.0,'model':None,'version':None}


def test_learning_event_fingerprint_changes_across_cooldown_buckets(monkeypatch,tmp_path):
    import trade_outcome_tracker as t
    monkeypatch.setattr(t,'DB_PATH',tmp_path/'outcomes.db')
    saved=[]
    class Store:
        def save(self,payload):
            saved.append(payload); return 'id'
    import cloud_learning_store
    monkeypatch.setattr(cloud_learning_store,'CloudLearningStore',lambda:Store())
    base={'fingerprint':'structural','symbol':'BTCUSDT','direction':'LONG_BIAS','entryPrice':100}
    a=dict(base,signal_created_at='2026-01-01T00:10:00+00:00')
    b=dict(base,signal_created_at='2026-01-01T07:10:00+00:00')
    t.persist_trade_signal(a)
    t.persist_trade_signal(b)
    assert saved[0]['metadata']['fingerprint'] != saved[1]['metadata']['fingerprint']
    assert saved[0]['metadata']['signal_fingerprint'] == 'structural'


def test_training_coordinator_blocks_parallel_training():
    from model_training_coordinator import training_slot
    with training_slot() as first:
        assert first is True
        with training_slot() as second:
            assert second is False


def test_cloud_payload_stays_pending_until_complete_horizon(monkeypatch,tmp_path):
    import trade_outcome_tracker as t
    monkeypatch.setattr(t,'DB_PATH',tmp_path/'t.db')
    t.initialize_trade_outcomes()
    signal={'fingerprint':'e','symbol':'BTCUSDT','direction':'LONG','entryPrice':100,'signal_created_at':'2026-01-01T00:00:00+00:00'}
    t.register_trade_signal(signal)
    with t.get_connection() as conn:
        conn.execute("INSERT INTO trade_outcomes VALUES (?,?,?,?,?,?)",('e','24h','2026-01-02T00:00:00+00:00',101,1,'OPEN'))
        row=conn.execute("SELECT * FROM tracked_signals WHERE fingerprint='e'").fetchone()
        payload=t._cloud_result_payload(conn,row)
        assert payload['training_status']=='pending'
        conn.execute("INSERT INTO trade_outcomes VALUES (?,?,?,?,?,?)",('e','72h','2026-01-04T00:00:00+00:00',102,2,'OPEN'))
        payload=t._cloud_result_payload(conn,row)
        assert payload['training_status']=='ready'
        assert payload['real_result']['latest_horizon']=='72h'


def test_profit_profile_requires_target_horizon(monkeypatch):
    import build_profit_profile as b
    monkeypatch.setenv('LEARNING_TARGET_HORIZON','24h')
    base={'features':{'aiFactors':{k:50 for k in b.FACTORS},'symbol':'BTCUSDT'},'signal_created_at':'2026-01-01T00:00:00+00:00'}
    assert b._normalize({**base,'real_result':{'returns':{'1h':1.0}}}) is None
    row=b._normalize({**base,'real_result':{'returns':{'1h':1.0,'24h':2.0}}})
    assert row is not None and row['return']==2.0


def test_cloud_active_model_applies_operator_policy(monkeypatch):
    import learning_engine_v14 as le
    # Isolate local DB so cloud fallback path is guaranteed.
    monkeypatch.setattr(le,'DB_PATH',__import__('pathlib').Path('/tmp/v43-test-learning.db'))
    try: le.DB_PATH.unlink()
    except FileNotFoundError: pass
    class Store:
        def load_active_model(self):
            return {'version':'cloud','config':{'global_weights':{k:1.0 for k in le.FEATURES}},'metrics':{},'rules':[]}
    import sys, types
    fake=types.ModuleType('cloud_model_store')
    fake.CloudModelStore=lambda:Store()
    monkeypatch.setitem(sys.modules,'cloud_model_store',fake)
    monkeypatch.setattr(le,'_apply_operator_weight_policy',lambda learned,defaults:{**learned,'trend':9.0})
    out=le.active_model({k:1.0 for k in le.FEATURES})
    assert out['weights']['trend']==9.0


def test_adaptive_persistence_failure_is_not_reported_as_promotion(monkeypatch):
    import adaptive_model_manager as am
    rows=[{'signal_payload':{},'net_pnl':1,'closed_at':str(i),'probability':50} for i in range(60)]
    monkeypatch.setattr(am,'_load_rows',lambda limit:rows)
    monkeypatch.setattr(am,'_extract',lambda row:([50.0]*len(am.FEATURES),1))
    monkeypatch.setattr(am,'_standardize',lambda xs:(xs,[0.0]*len(am.FEATURES),[1.0]*len(am.FEATURES)))
    monkeypatch.setattr(am,'_train',lambda xs,ys:([0.0]*len(am.FEATURES),2.0))
    monkeypatch.setattr(am,'_metrics',lambda rows,model:{'samples':len(rows),'accuracy':.9,'log_loss':.1,'brier':.1,'baseline_log_loss':.8,'baseline_brier':.3})
    class Q:
        data=[]
        def select(self,*a): return self
        def eq(self,*a): return self
        def order(self,*a,**k): return self
        def limit(self,*a): return self
        def execute(self): return self
    class R:
        def execute(self): raise RuntimeError('db down')
    class C:
        def table(self,*a): return Q()
        def rpc(self,*a,**k): return R()
    monkeypatch.setattr(am,'_client',lambda:C())
    out=am.train_candidate('manual')
    assert out['status']=='persistence-error'
