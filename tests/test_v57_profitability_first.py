from datetime import datetime, timezone
import json

import backfill_execution_dataset_v57 as bf
import ai_hedge_fund_engine as hedge
from build_profit_profile import build, validate_profile


def _paper(status='cancelled', fill_source=None, verified=None, opened=True):
    return {
        'id':'p1','status':status,'opened_at':'2026-09-01T00:00:00+00:00' if opened else None,
        'entry_price':100,'fill_price_source':fill_source,'execution_verified':verified,
        'symbol':'BTCUSDT','side':'LONG','created_at':'2026-09-01T00:00:00+00:00',
        'signal_entry_price':100,'signal_payload':{'setup':'PULLBACK'},'notional_usd':100,
    }


def test_cancelled_opened_row_is_no_fill_without_verified_source():
    s=bf._paper_sample(_paper())
    assert s['entry_status']=='no_fill'
    assert s['sample_type']=='PAPER_NO_FILL'
    assert s['filled_at'] is None and s['actual_entry'] is None


def test_cancelled_row_never_becomes_fill_even_with_stale_source():
    s=bf._paper_sample(_paper(fill_source='market_cross', verified=True))
    assert s['entry_status']=='no_fill'


def test_real_closed_fill_requires_fill_source():
    row=_paper(status='closed', fill_source='market_cross', verified=True)
    row.update(net_pnl=2.0, notional_usd=100, closed_at='2026-09-01T01:00:00+00:00', close_reason='TP1')
    s=bf._paper_sample(row)
    assert s['entry_status']=='filled'
    assert s['sample_type']=='PAPER_EXECUTION'
    assert s['net_return_pct']==2.0


def test_profile_separates_execution_from_research():
    obs=[{'fingerprint':'x','symbol':'BTCUSDT','signal_direction':'LONG_BIAS','signal_score':70,'signal_created_at':'2026-08-01T00:00:00+00:00','features':json.dumps({'fingerprint':'x','direction':'LONG_BIAS','setup':'PULLBACK','aiFactors':{'trend':70}}),'real_result':json.dumps({'horizon':'24h','return_percent':8})}]
    replay=[{'sample_id':'shadow:1','sample_type':'SHADOW_EXECUTION','fingerprint':'x','symbol':'BTCUSDT','direction':'LONG_BIAS','setup':'PULLBACK','signal_created_at':'2026-08-01T00:00:00+00:00','entry_status':'filled','outcome':'SL','net_return_pct':-2,'r_multiple':-1,'feature_payload':{'fingerprint':'x','direction':'LONG_BIAS','setup':'PULLBACK','aiFactors':{'trend':70}}}]
    p=build(obs,windows=[21],replay_rows=replay)
    ok,reasons=validate_profile(p)
    assert ok, reasons
    assert p['execution_overall']['samples']==1
    assert p['execution_overall']['robust_avg_return'] < 0
    assert p['research_overall']['samples']==0  # matching execution supersedes proxy truth


def _signal():
    return {'symbol':'BTCUSDT','direction':'LONG_BIAS','setup':'PULLBACK','score':90,'aiScore':90,'probability':90,'rr':3,'entryPrice':100,'stop':98,'tp1':106,'confidence':90,'uncertainty':10,'quoteVolume':500_000_000,'structure1h':'BOS_UP','marketRegime':'bull_trend','timeframes':{'1d':'UP','4h':'UP','1h':'UP','15m':'UP','5m':'UP'},'marketExchanges':['binance','bybit','okx'],'exchangeCount':3,'aiFactors':{'trend':90,'momentum':90,'volume':90,'funding':80,'alignment':90,'risk_reward':90,'capital_flow':80,'smart_money':80,'open_interest':60}}


def test_profitability_gate_blocks_great_signal_when_execution_edge_unproven(monkeypatch):
    ex={'samples':46,'win_rate':6.5,'robust_avg_return':-5.4,'robust_profit_factor':0.2,'probability_auc':0.32,'probability_brier':0.42}
    p={'schema_version':57,'version':'v57-test','target_type':'execution_only_profitability_v57','valid':True,'validation_reasons':[],'overall':ex,'execution_overall':ex,'research_overall':{'samples':1000,'win_rate':70},'groups':{},'execution_groups':{},'recent_windows':{'21':{}},'recent_execution_windows':{'21':{}},'recent_execution_overall':{'21':ex},'recent_overall':{'21':ex},'recent_rule_diagnostics':{},'rule_diagnostics':[]}
    monkeypatch.setattr(hedge,'_CACHE',p)
    monkeypatch.setattr(hedge,'_execution_calibration',lambda s:{'available':False,'samples':0})
    r=hedge.evaluate_signal(_signal())
    assert not r['qualityPassed']
    assert not r['profitabilityGate']['passed']


def test_no_execution_model_means_zero_fill_not_optimistic_70(monkeypatch):
    ex={'samples':200,'win_rate':55,'robust_avg_return':1.0,'robust_profit_factor':1.3,'probability_auc':0.60,'probability_brier':0.20}
    p={'schema_version':57,'version':'v57-test','target_type':'execution_only_profitability_v57','valid':True,'validation_reasons':[],'overall':ex,'execution_overall':ex,'research_overall':{'samples':0},'groups':{},'execution_groups':{},'recent_windows':{'21':{}},'recent_execution_windows':{'21':{}},'recent_execution_overall':{'21':ex},'recent_overall':{'21':ex},'recent_rule_diagnostics':{},'rule_diagnostics':[]}
    monkeypatch.setattr(hedge,'_CACHE',p)
    monkeypatch.setattr(hedge,'_execution_calibration',lambda s:{'available':False,'samples':0})
    # execution_model import returns whatever is on disk; force missing via sys.modules shim
    import execution_model_v57
    monkeypatch.setattr(execution_model_v57,'predict',lambda s:{'available':False,'status':'missing'})
    r=hedge.evaluate_signal(_signal())
    assert r['executableProbability']==0.0
    assert not r['qualityPassed']


def test_cloud_outcome_maps_horizon_label_to_schema_value():
    from trade_outcome_tracker import _canonical_cloud_outcome
    assert _canonical_cloud_outcome('HORIZON_SL')=='SL'
    assert _canonical_cloud_outcome('HORIZON_TP3')=='TP3'
    assert _canonical_cloud_outcome('OPEN')=='OPEN'


def test_sign_preserving_winsorization_keeps_rare_winners_positive():
    from build_profit_profile import _winsorized
    values=[-5.0]*40+[3.0,45.0,55.0]
    robust=_winsorized(values)
    assert sum(1 for x in robust if x>0)==3
    assert max(robust)<=25.0


def test_execution_model_rejects_all_filled_label_dataset(monkeypatch):
    import execution_model_v57 as em
    rows=[]
    for i in range(300):
        rows.append({'entry_status':'filled','outcome':'TP1' if i%2 else 'SL','net_return_pct':1.0 if i%2 else -1.0})
    monkeypatch.setattr(em,'_load_rows',lambda limit=20000:rows)
    r=em.train('test-label-quality')
    assert r['status']=='invalid-label-balance'
    assert r['label_quality']['no_fill']==0
    assert r['required']['no_fill'] >= 40


def test_profile_exposes_paper_and_shadow_execution_separately():
    paper=[{'id':'p','symbol':'BTCUSDT','side':'LONG','status':'closed','created_at':'2026-08-01T00:00:00+00:00','opened_at':'2026-08-01T00:05:00+00:00','closed_at':'2026-08-01T01:00:00+00:00','entry_price':100,'exit_price':102,'fill_price_source':'market_cross','execution_verified':True,'notional_usd':100,'net_pnl':2,'signal_payload':{'fingerprint':'p','setup':'PULLBACK','aiFactors':{'trend':70}}}]
    replay=[{'sample_id':'shadow:s','sample_type':'SHADOW_EXECUTION','fingerprint':'s','symbol':'ETHUSDT','direction':'LONG','setup':'PULLBACK','signal_created_at':'2026-08-01T00:00:00+00:00','entry_status':'filled','outcome':'SL','net_return_pct':-2,'r_multiple':-1,'feature_payload':{'fingerprint':'s','direction':'LONG','setup':'PULLBACK','aiFactors':{'trend':70}}}]
    p=build([],windows=[21],execution_rows=paper,replay_rows=replay)
    assert p['paper_execution_overall']['samples']==1
    assert p['shadow_execution_overall']['samples']==1
    assert p['execution_overall']['samples']==2


def test_profitability_gate_requires_paper_execution_evidence(monkeypatch):
    ex={'samples':500,'win_rate':60,'robust_avg_return':2.0,'robust_profit_factor':1.5,'probability_auc':0.62,'probability_brier':0.18}
    paper={'samples':20,'win_rate':70,'robust_avg_return':3.0,'robust_profit_factor':2.0}
    p={'schema_version':57,'version':'v57-test','target_type':'execution_only_profitability_v57','valid':True,'validation_reasons':[],'overall':ex,'execution_overall':ex,'paper_execution_overall':paper,'shadow_execution_overall':ex,'research_overall':{'samples':0},'groups':{},'execution_groups':{},'recent_windows':{'21':{}},'recent_execution_windows':{'21':{}},'recent_execution_overall':{'21':ex},'recent_overall':{'21':ex},'recent_rule_diagnostics':{},'rule_diagnostics':[]}
    monkeypatch.setattr(hedge,'_CACHE',p)
    monkeypatch.setattr(hedge,'_execution_calibration',lambda s:{'available':False,'samples':0})
    import execution_model_v57
    monkeypatch.setattr(execution_model_v57,'predict',lambda s:{'available':True,'fillProbability':80,'profitProbability':80,'expectedReturnPct':2,'uncertainty':5,'meanAuc':0.7})
    r=hedge.evaluate_signal(_signal())
    assert not r['qualityPassed']
    assert r['profitabilityGate']['executionPassed']
    assert not r['profitabilityGate']['paperPassed']


def test_adaptive_purged_split_keeps_dense_validation_segments(monkeypatch):
    import execution_model_v57 as em
    from datetime import timedelta
    base=datetime(2026,9,1,tzinfo=timezone.utc)
    rows=[]
    # 1000 dense signals over ~42h. A fixed 72h embargo erases the middle splits.
    for i in range(1000):
        ts=base+timedelta(minutes=2*i)
        rows.append({'signal_created_at':ts.isoformat(),'exit_at':(ts+timedelta(minutes=20)).isoformat()})
    monkeypatch.setenv('EXECUTION_ML_EMBARGO_HOURS','72')
    monkeypatch.setenv('EXECUTION_ML_MIN_EMBARGO_HOURS','1')
    split,meta=em._purged_split(rows,return_meta=True)
    assert split is not None, meta
    assert min(len(x) for x in split) >= 20
    assert 1 <= meta['effective_embargo_hours'] < 72
    assert len(meta['attempts']) > 1


def test_purged_split_reports_failure_instead_of_silent_none(monkeypatch):
    import execution_model_v57 as em
    from datetime import timedelta
    base=datetime(2026,9,1,tzinfo=timezone.utc)
    rows=[]
    for i in range(120):
        ts=base+timedelta(seconds=i)
        rows.append({'signal_created_at':ts.isoformat(),'exit_at':(ts+timedelta(seconds=1)).isoformat()})
    monkeypatch.setenv('EXECUTION_ML_EMBARGO_HOURS','72')
    monkeypatch.setenv('EXECUTION_ML_MIN_EMBARGO_HOURS','1')
    split,meta=em._purged_split(rows,min_segment_samples=20,return_meta=True)
    assert split is None
    assert meta['reason']=='segment_too_small_after_min_embargo'
    assert meta['attempts']



def test_v57_2_gate_failures_explain_runtime_and_champion_rejection(monkeypatch):
    import execution_model_v57 as em
    monkeypatch.setenv('EXECUTION_ML_MIN_AUC','0.56')
    monkeypatch.setenv('EXECUTION_ML_CHAMPION_MIN_AUC','0.60')
    monkeypatch.setenv('EXECUTION_RETURN_CHAMPION_MIN_PF','1.10')
    failures=em._gate_failures(
        auc=0.70,brier=0.20,baseline_auc=0.50,baseline_brier=0.30,base_rate_brier=0.30,
        champion_auc=0.55,champion_brier=0.21,champion_precision20=0.60,
        champion_baseline_auc=0.50,champion_baseline_brier=0.30,
        champion_base_rate_brier=0.30,champion_baseline_precision20=0.50,
        reg_metrics={'return_mae':1.0,'baseline_return_mae':2.0,'return_spearman':0.2,
                     'return_sign_accuracy':0.6,'champion_return_spearman':0.2,
                     'champion_return_sign_accuracy':0.6,'champion_return_pf':0.8})
    assert failures['runtime']==[]
    assert 'champion_auc_below_floor' in failures['champion']
    assert 'champion_return_pf' in failures['champion']


def test_v57_2_compact_summary_keeps_top_models_and_failure_counts():
    import execution_model_v57 as em
    models={'GLOBAL':{'fill':[{}], 'outcome':[
        {'auc':0.70,'champion_auc':0.55,'runtime_ok':True,'champion_ok':False,'gate_failures':{'runtime':[],'champion':['champion_auc_below_floor']}},
        {'auc':0.60,'champion_auc':0.65,'runtime_ok':True,'champion_ok':True,'gate_failures':{'runtime':[],'champion':[]}},
    ], 'rejections':[{'reason':'x'}]}}
    summary,failures=em._compact_model_summary(models)
    assert summary['GLOBAL']['trained_outcome']==2
    assert summary['GLOBAL']['healthy_outcome']==2
    assert summary['GLOBAL']['champion_outcome']==1
    assert summary['GLOBAL']['top_selection'][0]['auc']==0.70
    assert summary['GLOBAL']['top_champion'][0]['champion_auc']==0.65
    assert failures['champion_auc_below_floor']==1

def test_v58_robust_winsor_clips_return_outlier(monkeypatch):
    import execution_model_v57 as m
    monkeypatch.setenv('EXECUTION_RETURN_WINSOR_LOW','0.05')
    monkeypatch.setenv('EXECUTION_RETURN_WINSOR_HIGH','0.95')
    vals,lo,hi=m._winsor([-100,-2,-1,0,1,2,100])
    assert min(vals) >= lo and max(vals) <= hi
    assert vals[0] > -100 and vals[-1] < 100

def test_v58_utility_threshold_uses_profitable_selection(monkeypatch):
    import execution_model_v57 as m
    monkeypatch.setenv('EXECUTION_RETURN_MIN_UTILITY_TRADES','10')
    pred=list(range(20))
    actual=[-2.0]*10+[1.0]*10
    u=m._utility_threshold(pred,actual)
    assert u is not None
    assert u['trades'] >= 10
    assert u['expectancy'] > 0
    assert u['profit_factor'] > 1
