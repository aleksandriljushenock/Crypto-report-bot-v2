import execution_model_v57 as m


def _row(ret=1.0, hour=12, vol=60, trend=60, regime='bull'):
    return {
        'signal_created_at': f'2026-01-01T{hour:02d}:00:00+00:00',
        'entry_status':'filled', 'outcome':'TP', 'net_return_pct':ret,
        'feature_payload': {
            'marketRegime': regime,
            'aiFactors': {'trend':trend,'momentum':60,'volume':60,'volatility':vol,'sentiment':55,'funding':50,'open_interest':55,'liquidations':50,'alignment':60,'risk_reward':60,'capital_flow':55,'smart_money':55,'narrative':55},
            'timeframes': {'1h':'UP','15m':'UP','5m':'UP'},
            'quoteVolume':1000000, 'rr':2.0, 'uncertainty':20,
        }
    }


def test_regime_vector_is_decision_time_and_stable_shape():
    a=m._regime_vector(_row(hour=2))
    b=m._regime_vector(_row(hour=18))
    assert len(a)==len(b)
    assert len(a)>20
    assert a!=b


def test_joint_selector_can_use_regime_and_ood(monkeypatch):
    monkeypatch.setenv('EXECUTION_RETURN_MIN_UTILITY_TRADES','10')
    pred=[1.0]*30
    prob=[.8]*30
    regime=[.9]*10+[.6]*10+[.1]*10
    ood=[.5]*20+[5.0]*10
    actual=[2.0]*8+[-1.0]*2 + [1.0]*6+[-1.0]*4 + [-2.0]*8+[1.0]*2
    u=m._utility_threshold(pred,actual,prob,regime,ood,1.5)
    assert u is not None
    assert 'regime_probability_threshold' in u
    assert u['ood_threshold']==1.5
    assert u['expectancy']>0
    assert u['profit_factor']>1


def test_bootstrap_expectancy_ci_positive_for_clear_edge(monkeypatch):
    monkeypatch.setenv('EXECUTION_BOOTSTRAP_REPS','120')
    lo,hi=m._bootstrap_expectancy_ci([1.0]*80+[-0.2]*20,seed=1)
    assert lo is not None and hi is not None
    assert lo>0
    assert hi>lo


def test_ood_profile_scores_outlier_higher():
    train=[_row(vol=50+i%3,trend=55+i%2) for i in range(100)]
    profile=m._ood_profile(train)
    normal=m._ood_scores(profile,[_row(vol=51,trend=56)])[0]
    outlier=m._ood_scores(profile,[_row(vol=500,trend=-200)])[0]
    assert outlier>normal


def test_specialist_veto_does_not_fallback_to_global(monkeypatch):
    bundle={'status':'champion','version':'x','models':{
        'BREAKOUT|LONG':{'key':'BREAKOUT|LONG','outcome':[{'runtime_ok':True,'champion_ok':True}],'fill':[],'fill_prior':.5},
        'GLOBAL':{'key':'GLOBAL','outcome':[{'runtime_ok':True,'champion_ok':True}],'fill':[],'fill_prior':.5},
    }}
    monkeypatch.setattr(m,'_load_bundle',lambda:bundle)
    calls=[]
    def fake(models,x,signal=None,require_champion=False):
        calls.append(models)
        return None if require_champion else None
    monkeypatch.setattr(m,'_ensemble',fake)
    sig={'setup':'BREAKOUT','direction':'LONG','feature_payload':{}}
    r=m.predict(sig)
    assert r['available'] is False
    assert r['status']=='specialist-veto-or-no-validated-outcome-model'
    # exactly one outcome attempt: no GLOBAL rescue after the specialist veto
    assert len(calls)==2  # outcome + fill for specialist only
