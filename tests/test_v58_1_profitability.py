import execution_model_v57 as m

def test_economic_stats_profitable():
    s=m._economic_stats([1.0,-0.25,0.5])
    assert s['profit_factor'] > 1
    assert s['expectancy'] > 0
    assert s['trades'] == 3

def test_walk_forward_insufficient_is_fail_closed():
    r=m._walk_forward_utility([], list(range(len(m.FEATURE_NAMES))), 'hgb', 1, 0.0)
    assert r['ok'] is False
    assert r['reason']=='walk_forward_insufficient_rows'
