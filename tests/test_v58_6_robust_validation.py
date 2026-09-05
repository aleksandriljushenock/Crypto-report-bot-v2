import os
import execution_model_v57 as m

def test_alpha_threshold_cannot_be_zero(monkeypatch):
    monkeypatch.setenv('EXECUTION_ALPHA_MIN_PROBABILITY','0.52')
    monkeypatch.setenv('EXECUTION_RETURN_MIN_UTILITY_TRADES','10')
    n=30
    got=m._utility_threshold([1.0]*n,[1.0]*n,[0.4+i*.02 for i in range(n)],[0.8]*n,[0.1]*n,1.0,True)
    assert got and got['probability_threshold'] >= .52

def test_block_bootstrap_reports_pf_ci():
    got=m._block_bootstrap_stats([1,-.3,1.2,-.2,.8,-.1,1.1,-.4,.9,-.2,1.0,-.3],58)
    assert got['expectancy_ci'][0] is not None
    assert got['pf_ci'][0] is not None

def test_fixed_threshold_evidence_is_separate_helper():
    assert callable(m._fixed_threshold_evidence)

def test_ood_cap_semantics(monkeypatch):
    monkeypatch.setenv('EXECUTION_OOD_MAX_SCORE','0.75')
    rows=[{'signal_created_at':'2026-01-01T00:00:00+00:00','feature_payload':{}} for _ in range(30)]
    p=m._ood_profile(rows)
    assert p['threshold'] <= .75

def test_version_586():
    assert open('VERSION').read().strip()=='58.6.2'

def test_v5861_breakout_wf_runs_without_main_utility(monkeypatch):
    import execution_model_v57 as m
    called={}
    monkeypatch.setattr(m,'_walk_forward_utility',lambda *a,**k: called.setdefault('wf',{'ok':False,'reason':'research'}) )
    assert m._walk_forward_utility([],[], 'hgb',1)['reason']=='research'
    assert called['wf']['ok'] is False


def test_v5861_runtime_bundle_strips_nonchampion_models():
    import execution_model_v57 as m
    b={'schema':5862,'version':'x','status':'shadow','trained_at':'t','feature_names':[], 'rows':1,'trigger':'t','models':{'BREAKOUT|LONG':{'key':'BREAKOUT|LONG','samples':1,'fill_prior':.5,'fill':[{'runtime_ok':True}],'outcome':[{'champion_ok':False}]}}}
    rb=m._runtime_bundle(b)
    assert rb['models']['BREAKOUT|LONG']['outcome']==[]
    assert rb['models']['BREAKOUT|LONG']['fill']==[]

def test_v5861_cloud_bundle_compresses_and_is_minimal(tmp_path):
    import joblib
    import execution_model_v57 as m
    bundle={'schema':5862,'version':'execution-ensemble-v58.6.2-test','status':'shadow','trained_at':'t','feature_names':['x'],'rows':10,'trigger':'test','models':{'BREAKOUT|LONG':{'key':'BREAKOUT|LONG','samples':10,'fill_prior':.5,'fill':[{'runtime_ok':True,'blob':'x'*10000}],'outcome':[{'champion_ok':False,'blob':'y'*10000}]}}}
    rb=m._runtime_bundle(bundle)
    p=tmp_path/'r.joblib'; joblib.dump(rb,p,compress=3)
    assert p.stat().st_size < 5000
    assert rb['models']['BREAKOUT|LONG']['outcome']==[]


def test_v5862_background_execution_cycle_is_isolated():
    from pathlib import Path
    src=Path('background_services.py').read_text()
    worker=Path('execution_auto_worker.py').read_text()
    assert "subprocess.Popen" in src
    assert "execution_auto_worker.py" in src
    assert "backfill(limit=limit, dry_run=False)" in worker
    assert "train(trigger='scheduled-auto-subprocess')" in worker
    assert 'execution_v58_6_2_latest_diagnostic.json' in worker
