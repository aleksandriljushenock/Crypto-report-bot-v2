import types


def test_v52_version():
    from pathlib import Path
    assert Path('VERSION').read_text().strip() in {'52.0.0','53.0.0','54.0.0','55.0.0','56.0.0','57.0.0','57.1.0','57.2.0','58.0.0','58.1.0','58.2.0'}


def test_final_gate_uses_post_model_probability(monkeypatch):
    import ai_intelligence
    monkeypatch.setenv('HEDGE_QUALITY_GATE_ENABLED','true')
    monkeypatch.setenv('HEDGE_MIN_QUALITY','70')
    monkeypatch.setenv('HEDGE_MIN_EV_PCT','0')
    monkeypatch.setenv('TRADE_MIN_PROBABILITY','70')
    monkeypatch.setenv('TRADE_MIN_RR','2')
    monkeypatch.setattr(ai_intelligence,'enrich_signal',lambda s:dict(s))
    monkeypatch.setattr(ai_intelligence,'save_ai_score',lambda s:None)
    import sys
    monkeypatch.setitem(sys.modules,'chronos_forecaster',types.SimpleNamespace(apply_to_finalists=lambda xs:xs))
    monkeypatch.setitem(sys.modules,'ai_hedge_fund_engine',types.SimpleNamespace(evaluate_signal=lambda s:{'qualityScore':80,'expectedValuePct':5,'calibratedProbability':66}))
    signal={'symbol':'X','probability':85,'rr':3,'qualityRules':[],'setup':'BREAKOUT','signalProfile':'BREAKOUT'}
    assert ai_intelligence.rank_signals([signal]) == []
    assert 'Probability' in ai_intelligence.get_last_rank_diagnostics()['rejected'][0]['reason']


def test_execution_target_overrides_mark_to_market(monkeypatch):
    import learning_engine_v14 as le
    sample={'fingerprint':'fp','symbol':'X','timeframe':'15m','direction':'LONG','created_at':'2026-08-30T00:00:00+00:00','old_score':80,
            'factors':{k:50.0 for k in le.FEATURES},'returns':{'24h':12.0}}
    monkeypatch.setattr(le,'_cloud_samples',lambda:[])
    monkeypatch.setattr(le,'_paper_learning_samples',lambda:[])
    monkeypatch.setattr(le,'_execution_samples',lambda:{'fp':{'return':-20.0,'win':0.0,'r_multiple':-1.0,'net_pnl':-2.0,'close_reason':'SL'}})
    monkeypatch.setattr(le,'_dedupe_samples',lambda xs:xs)
    import sys
    class Ctx:
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def execute(self,*a,**k):
            class R:
                def fetchall(self): return []
            return R()
    fake=types.SimpleNamespace(initialize_trade_outcomes=lambda:None,get_connection=lambda:Ctx())
    monkeypatch.setitem(sys.modules,'trade_outcome_tracker',fake)
    # inject via cloud path so grouped receives sample
    monkeypatch.setattr(le,'_cloud_samples',lambda:[sample])
    rows=le.load_samples()
    assert rows[0]['target_source']=='paper_execution'
    assert rows[0]['win']==0.0
    assert rows[0]['return']==-20.0


def test_chronos_is_shadow_only_by_default(monkeypatch):
    import chronos_forecaster as cf
    monkeypatch.delenv('CHRONOS_PROBABILITY_BLEND_ENABLED',raising=False)
    signal={'direction':'LONG_BIAS','probability':60,'confidence':50}
    out=cf.blend_signal(signal,{'probabilityUp':90,'forecastReturnPct':2,'uncertaintyPct':1,'model':'x'})
    assert out['probability']==60
    assert out['chronos']['weight']==0
    assert out['chronos']['shadowOnly'] is True
