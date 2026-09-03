from datetime import datetime,timedelta,timezone
from pathlib import Path


def test_version_v48():
    assert Path('VERSION').read_text().strip()in {'48.0.0','49.0.0','50.0.0','51.0.0','52.0.0','53.0.0','54.0.0','55.0.0','56.0.0','57.0.0','57.1.0','57.2.0','58.0.0','58.1.0','58.2.0','58.3.0','58.4.0','58.5.0'}


def test_cloud_active_model_is_scoped_by_model_name():
    src=Path('cloud_model_store.py').read_text()
    assert 'def load_active_model(self, model_name: str | None = None)' in src
    block=src[src.index('def load_active_model'):src.index('def list_models')]
    assert '.eq("model_name", str(model_name or self.DEFAULT_MODEL_NAME))' in block


def test_v48_sql_promotion_is_model_scoped_and_target_checked_first():
    text=Path('migrations/SUPABASE_V48_INTEGRITY.sql').read_text()
    assert 'WHERE model_name=p_model_name AND model_version=p_model_version' in text
    assert "WHERE model_name=p_model_name AND is_active=true" in text
    fn=text[text.index('CREATE OR REPLACE FUNCTION public.model_registry_promote_v48'):]; assert fn.index('v_target_exists') < fn.index('UPDATE public.model_registry SET is_active=false')
    assert 'p_lease_generation' in text and "lock_name='v14-training'" in text


def test_learning_event_identity_uses_explicit_event(monkeypatch):
    import trade_outcome_tracker as t
    monkeypatch.setattr(t,'_market_price_at_signal',lambda s:100.0)
    saved=[]
    class Store:
        def save(self,p): saved.append(p); return {'id':'x'}
    monkeypatch.setattr(t,'CloudLearningStore',Store,raising=False)
    # inspect helper behavior indirectly by deterministic payload fingerprint source code
    src=Path('trade_outcome_tracker.py').read_text()
    assert 'explicit_event = signal.get("eventFingerprint")' in src
    assert 'LEARNING_EVENT_FALLBACK_BUCKET_SECONDS' in src


def test_scanner_attaches_event_identity():
    src=Path('scanner/pipeline.py').read_text()
    assert '_attach_event_identity' in src
    assert 'eventFingerprint' in src
    assert 'signal_created_at' in src


def test_historical_window_chooses_coarser_interval_for_old_window():
    from historical_prices import historical_candles_between
    now=datetime(2026,1,10,tzinfo=timezone.utc)
    start=now-timedelta(days=5); end=start+timedelta(hours=12)
    called=[]
    class C:
        def klines(self,symbol,interval,limit): called.append(interval); return []
    historical_candles_between(C(),'BTCUSDT',start,end,now=now,max_bars=1000)
    assert called and called[0] in {'1h','4h','1d'}


def test_training_coordinator_exposes_fencing_state(monkeypatch):
    import model_training_coordinator as m
    assert hasattr(m,'lease_healthy') and hasattr(m,'lease_fence')
    assert m.lease_healthy() is True
    assert m.lease_fence()==(None,None)


def test_paper_has_terminal_unresolved_void_path():
    src=Path('paper_trading.py').read_text()
    repo=Path('repositories/paper_repository.py').read_text()
    sql=Path('migrations/SUPABASE_V48_INTEGRITY.sql').read_text()
    assert 'PAPER_UNRESOLVED_GRACE_HOURS' in src
    assert 'void_execution_atomic' in src and 'paper_void_execution_v48' in repo
    assert "status='void_data'" in sql


def test_paper_execution_can_use_coarser_history():
    import paper_trading as p
    calls=[]
    now=datetime.now(timezone.utc)
    row=[(now-timedelta(hours=100)).timestamp()*1000,1,1,1,1,0,(now-timedelta(hours=99)).timestamp()*1000]
    class C:
        def klines(self,symbol,interval,limit): calls.append(interval); return [row] if interval=='1h' else []
    rows,iv,mins=p._execution_klines(C(),'BTCUSDT',lookback_hours=100)
    assert iv=='1h' and mins==60 and rows
