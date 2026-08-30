from datetime import datetime, timezone
import ai_hedge_fund_engine as hedge
from backfill_execution_dataset_v54 import _resolve
from execution_features_v54 import extract, FEATURE_NAMES
from execution_model_v54 import _fit_one


def _candle(ts, high, low, close):
    ms=int(ts.timestamp()*1000)
    return [ms,100,high,low,close,0,ms+300000]


def test_v54_same_candle_tp_sl_is_conservative_sl():
    t=datetime(2026,8,1,tzinfo=timezone.utc)
    row={'direction':'LONG_BIAS','actual_entry':100,'stop':98,'tp1':104,'tp2':108,'tp3':112,'filled_at':t.isoformat()}
    r=_resolve(row,[_candle(t,105,97,103)])
    assert r['outcome']=='SL'
    assert r['net_return_pct'] < 0


def test_v54_short_first_hit_geometry():
    t=datetime(2026,8,1,tzinfo=timezone.utc)
    row={'direction':'SHORT_BIAS','actual_entry':100,'stop':102,'tp1':96,'tp2':92,'tp3':88,'filled_at':t.isoformat()}
    r=_resolve(row,[_candle(t,100.5,95.5,96)])
    assert r['outcome']=='TP1'
    assert r['net_return_pct'] > 0


def test_v54_feature_schema_does_not_include_outcome_fields():
    assert not any(x in FEATURE_NAMES for x in ('outcome','net_return_pct','exit_reason','mfe_pct','mae_pct'))
    row={'direction':'LONG_BIAS','setup':'PULLBACK','feature_payload':{'aiFactors':{'trend':80},'probability':70}}
    assert len(extract(row))==len(FEATURE_NAMES)


def test_v54_severe_health_is_or_based(monkeypatch):
    profile={'schema_version':54,'version':'x','target_type':'execution_first_v54','valid':True,'validation_reasons':[],
      'overall':{'win_rate':50},'groups':{},'recent_windows':{'21':{}},
      'recent_overall':{'21':{'samples':100,'execution_samples':40,'execution_win_rate':10,'execution_robust_avg_return':-2,'execution_robust_profit_factor':0.2,'execution_probability_auc':0.60,'execution_probability_brier':0.40}}}
    monkeypatch.setattr(hedge,'_CACHE',profile)
    h=hedge._profile_health()
    assert h['severe'] and h['status']=='SEVERE'
    assert 'execution_profit_factor_critical' in h['reasons']


def test_v54_unprofitable_pullback_is_setup_blocked(monkeypatch):
    stat={'samples':120,'win_rate':45,'robust_avg_return':1,'robust_profit_factor':1.2,'execution_samples':30,'execution_win_rate':10,'execution_robust_avg_return':-3,'execution_robust_profit_factor':0.1}
    profile={'schema_version':54,'version':'x','target_type':'execution_first_v54','valid':True,'validation_reasons':[],
      'overall':{'win_rate':45,'execution_win_rate':10},'groups':{'setup_direction':{'PULLBACK|LONG':stat}},'recent_windows':{},'recent_overall':{}}
    monkeypatch.setattr(hedge,'_CACHE',profile)
    g=hedge._setup_guard({'setup':'PULLBACK','direction':'LONG_BIAS'})
    assert g['blocked']
    assert g['reason']=='negative_execution_specialist'


def test_v54_gradient_model_beats_flat_probability_on_temporal_holdout(monkeypatch):
    rows=[]
    for i in range(260):
        win=(i%4) in (2,3)
        trend=90 if win else 10
        rows.append({'entry_status':'filled','outcome':'TP1' if win else 'SL','net_return_pct':2 if win else -1,
          'direction':'LONG_BIAS','setup':'PULLBACK','signal_created_at':f'2026-08-{1+(i//24)%20:02d}T00:00:00+00:00',
          'feature_payload':{'probability':50,'aiScore':50,'rr':2.5,'aiFactors':{'trend':trend,'momentum':trend,'volume':70,'volatility':50,'sentiment':50,'funding':50,'open_interest':50,'liquidations':50,'alignment':trend,'risk_reward':70,'capital_flow':50,'smart_money':50,'narrative':50},'timeframes':{'1d':'UP'}}})
    monkeypatch.setenv('EXECUTION_ML_MIN_SAMPLES','100')
    monkeypatch.setenv('EXECUTION_ML_MIN_AUC','0.54')
    m=_fit_one(rows,'outcome',250)
    assert m is not None
    assert m['auc'] > 0.9
    assert m['runtime_ok']
