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
    assert open('VERSION').read().strip()=='58.6.0'
