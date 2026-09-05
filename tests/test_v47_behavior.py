from datetime import datetime, timedelta, timezone
from pathlib import Path


def test_version_v47():
    assert Path('VERSION').read_text().strip() in {'47.0.0','48.0.0','49.0.0','50.0.0','51.0.0','52.0.0','53.0.0','54.0.0','55.0.0','56.0.0','57.0.0','57.1.0','57.2.0','58.0.0','58.1.0','58.2.0','58.3.0','58.4.0','58.5.0','58.6.0','58.6.1'}


def test_paper_event_fingerprint_reusable_later(monkeypatch):
    import paper_trading as p
    a=datetime(2026,1,1,12,0,tzinfo=timezone.utc)
    b=a+timedelta(hours=1)
    signal={'fingerprint':'structural'}
    assert p._paper_event_fingerprint('structural',signal,a) != p._paper_event_fingerprint('structural',signal,b)
    explicit={'fingerprint':'structural','signal_created_at':a.isoformat()}
    assert p._paper_event_fingerprint('structural',explicit,a) == p._paper_event_fingerprint('structural',explicit,b)


def test_paper_execution_ignores_unfinished_candle(monkeypatch):
    import paper_trading as p
    now=datetime(2026,1,1,12,0,30,tzinfo=timezone.utc)
    monkeypatch.setattr(p,'_now',lambda:now)
    start=datetime(2026,1,1,12,0,tzinfo=timezone.utc)
    row=[start.timestamp()*1000,100,110,90,105,0,(start+timedelta(minutes=1)).timestamp()*1000]
    assert p._iter_execution_candles([row],since=start-timedelta(minutes=1),interval_minutes=1)==[]


def test_shadow_same_structure_can_be_new_event(tmp_path, monkeypatch):
    import shadow_signals as s
    monkeypatch.setattr(s,'DB_PATH',tmp_path/'shadow.db')
    monkeypatch.setattr(s,'_cloud_enabled',lambda:False)
    base=datetime(2026,1,1,12,0,tzinfo=timezone.utc)
    current=[base]
    monkeypatch.setattr(s,'_now',lambda:current[0])
    item={'fingerprint':'same','symbol':'BTCUSDT','direction':'LONG_BIAS','setup':'PULLBACK','entryPrice':100,'reason':'quality'}
    assert s.register_shadow_candidates([item])==1
    current[0]=base+timedelta(hours=1)
    assert s.register_shadow_candidates([item])==1


def test_shadow_not_observed_without_all_horizons(tmp_path, monkeypatch):
    import shadow_signals as s
    monkeypatch.setattr(s,'DB_PATH',tmp_path/'shadow.db')
    monkeypatch.setattr(s,'_cloud_enabled',lambda:False)
    now=datetime(2026,1,3,12,0,tzinfo=timezone.utc)
    monkeypatch.setattr(s,'_now',lambda:now)
    s.initialize()
    filled=now-timedelta(hours=30)
    with s._conn() as c:
        c.execute("INSERT INTO shadow_signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('x','BTCUSDT','LONG_BIAS','PULLBACK','r','t',(filled-timedelta(hours=1)).isoformat(),now.isoformat(),'filled',100,100,filled.isoformat(),90,110,None,None,1,1,1,1,'{}',now.isoformat()))
    class C:
        def klines(self,*a,**k): return []
    monkeypatch.setattr(s,'create_trade_market_client',lambda:C())
    monkeypatch.setattr(s,'historical_price_at',lambda *a,**k:None)
    s.update_shadow_signals()
    with s._conn() as c:
        assert c.execute("select status from shadow_signals where id='x'").fetchone()['status']=='filled'


def test_invalid_learning_baseline_not_saved(monkeypatch):
    import trade_outcome_tracker as t
    monkeypatch.setattr(t,'_market_price_at_signal',lambda signal:None)
    assert t.persist_trade_signal({'fingerprint':'x','symbol':'BTCUSDT','direction':'LONG_BIAS','entryPrice':100}) is None


def test_directional_calibration_preferred(monkeypatch):
    import learning_engine_v14 as l
    monkeypatch.setattr(l,'_runtime_env',lambda k,d:d)
    model={'config':{'calibration':{'bull':[{'score_min':0,'score_max':100,'samples':100,'probability':.6}], 'bull:LONG':[{'score_min':0,'score_max':100,'samples':100,'probability':.8}]}}}
    prob,_=l.calibrated_probability(50,'bull',model,'LONG_BIAS')
    assert prob==.8


def test_v47_migration_has_renew_status_and_atomic_model_promotion():
    text=Path('migrations/SUPABASE_V47_INTEGRITY.sql').read_text()
    assert 'model_training_lease_renew_v47' in text
    assert 'model_training_lease_running_v47' in text
    assert 'model_registry_promote_v47' in text
    assert 'uq_model_registry_one_active_v47' in text
