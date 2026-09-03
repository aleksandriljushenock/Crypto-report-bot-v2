from datetime import datetime, timezone, timedelta
import execution_model_v57 as m

def test_v585_version():
    assert open('VERSION',encoding='utf-8').read().strip()=='58.5.0'

def test_adaptive_inner_plan_expands_and_preserves_train(monkeypatch):
    monkeypatch.setenv('EXECUTION_WF_INNER_MIN_ROWS','60')
    plans=m._adaptive_inner_plan([{}]*600,240)
    assert len(plans)>=2
    assert plans[0][0]>=60
    assert all(600-s-c>=240 for s,c,_ in plans)

def test_wf_purge_removes_overlapping_outcome(monkeypatch):
    monkeypatch.setenv('EXECUTION_WF_EMBARGO_HOURS','1')
    b=datetime(2026,1,2,tzinfo=timezone.utc)
    hist=[{'signal_created_at':(b-timedelta(hours=10)).isoformat(),'exit_at':(b-timedelta(hours=2)).isoformat()},
          {'signal_created_at':(b-timedelta(hours=5)).isoformat(),'exit_at':(b+timedelta(hours=1)).isoformat()}]
    test=[{'signal_created_at':b.isoformat()}]
    kept,purged,emb=m._wf_purge_history(hist,test)
    assert len(kept)==1 and purged==1 and emb==1.0
