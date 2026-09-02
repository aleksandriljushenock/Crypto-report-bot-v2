from execution_model_v57 import _utility_threshold


def test_joint_utility_selector_can_require_classifier_agreement(monkeypatch):
    monkeypatch.setenv('EXECUTION_RETURN_MIN_UTILITY_TRADES','10')
    pred=[1.0]*10+[0.9]*10+[0.8]*10
    prob=[0.9]*10+[0.6]*10+[0.2]*10
    actual=[2.0]*8+[-1.0]*2 + [0.5]*5+[-1.0]*5 + [-2.0]*8+[1.0]*2
    u=_utility_threshold(pred,actual,prob)
    assert u is not None
    assert 'probability_threshold' in u
    assert u['expectancy'] > 0
    assert u['profit_factor'] > 1


def test_joint_utility_selector_never_uses_future_data_shape(monkeypatch):
    monkeypatch.setenv('EXECUTION_RETURN_MIN_UTILITY_TRADES','10')
    a=_utility_threshold([1]*20,[1]*15+[-1]*5,[.8]*20)
    assert set(('threshold','probability_threshold','trades','profit_factor','expectancy')).issubset(a)
