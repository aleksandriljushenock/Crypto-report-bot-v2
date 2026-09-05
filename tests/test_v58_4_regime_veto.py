import execution_model_v57 as m

def test_regime_selector_has_nonzero_floor(monkeypatch):
    monkeypatch.setenv("EXECUTION_RETURN_MIN_UTILITY_TRADES","10")
    monkeypatch.setenv("EXECUTION_REGIME_MIN_PROBABILITY","0.45")
    pred=[1.0]*30; prob=[.8]*30; regime=[.9]*10+[.6]*10+[.2]*10; actual=[2.0]*8+[-1.0]*2+[1.0]*6+[-1.0]*4+[-2.0]*8+[1.0]*2
    u=m._utility_threshold(pred,actual,prob,regime,[.2]*30,1.5,require_regime=True)
    assert u is not None
    assert u["regime_probability_threshold"] >= .45

def test_ood_threshold_can_be_tightened(monkeypatch):
    monkeypatch.setenv("EXECUTION_RETURN_MIN_UTILITY_TRADES","10")
    pred=[1.0]*30; prob=[.8]*30; regime=[.8]*30
    ood=[.2]*20+[1.4]*10; actual=[1.0]*18+[-.2]*2+[-2.0]*10
    u=m._utility_threshold(pred,actual,prob,regime,ood,1.5,require_regime=True)
    assert u is not None
    assert u["ood_threshold"] <= 1.5

def test_v584_version_file():
    assert open("VERSION",encoding="utf-8").read().strip()in {"58.4.0","58.5.0","58.6.0","58.6.1","58.6.2","58.6.3"}
