from datetime import datetime, timedelta, timezone
import pytest


def _candle(start, minutes=1):
    end = start + timedelta(minutes=minutes)
    return [int(start.timestamp()*1000), 100, 101, 99, 100, 0, int(end.timestamp()*1000)]


def test_paper_cursor_stops_at_history_gap(monkeypatch):
    import paper_trading as p
    now = datetime(2026,1,1,14,0,tzinfo=timezone.utc)
    monkeypatch.setattr(p, '_now', lambda: now)
    previous = datetime(2026,1,1,12,0,tzinfo=timezone.utc)
    rows = [_candle(datetime(2026,1,1,13,0,tzinfo=timezone.utc))]
    assert p._next_paper_cursor(previous, rows, interval_minutes=1) == previous


def test_paper_cursor_advances_only_contiguous(monkeypatch):
    import paper_trading as p
    now = datetime(2026,1,1,14,0,tzinfo=timezone.utc)
    monkeypatch.setattr(p, '_now', lambda: now)
    previous = datetime(2026,1,1,12,0,tzinfo=timezone.utc)
    rows = [_candle(previous), _candle(previous+timedelta(minutes=1)), _candle(previous+timedelta(minutes=4))]
    assert p._next_paper_cursor(previous, rows, interval_minutes=1) == previous+timedelta(minutes=2)


def test_cloud_update_zero_rows_is_failure():
    from cloud_learning_store import CloudLearningStore
    class Resp: data=[]
    class Q:
        def update(self,*a,**k): return self
        def eq(self,*a,**k): return self
        def execute(self): return Resp()
    class C:
        def table(self,*a): return Q()
    s=CloudLearningStore.__new__(CloudLearningStore); s.client=C()
    assert s.update_by_id('missing', {'training_status':'ready'}) is False


def test_cloud_lookup_error_is_fail_closed():
    from cloud_learning_store import CloudLearningStore
    class Q:
        def select(self,*a): return self
        def contains(self,*a): return self
        def limit(self,*a): return self
        def execute(self): raise TimeoutError('down')
    class C:
        def table(self,*a): return Q()
    s=CloudLearningStore.__new__(CloudLearningStore); s.client=C()
    with pytest.raises(RuntimeError): s.find_by_fingerprint('abc')


def test_v14_version_format_has_microseconds():
    import learning_engine_v14 as m
    from pathlib import Path
    src = Path(m.__file__).read_text(encoding='utf-8')
    assert '%Y%m%d%H%M%S%f' in src


def test_env_examples_have_no_duplicate_keys():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    for name in ('.env.example','.env.vps.example'):
        keys=[]
        for line in (root/name).read_text().splitlines():
            if '=' in line and line and not line.startswith('#'):
                k=line.split('=',1)[0].strip()
                if k.replace('_','').isalnum(): keys.append(k)
        assert len(keys)==len(set(keys)), name
