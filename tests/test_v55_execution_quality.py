from datetime import datetime, timezone
import json

from execution_features_v55 import extract, FEATURE_NAMES
from execution_model_v55 import _purged_split
from backfill_execution_dataset_v55 import _resolve


def _candle(ts, high, low, close, minutes=5):
    ms=int(ts.timestamp()*1000)
    return (ms,high,low,close,ms+minutes*60000)


def test_v55_full_shadow_features_are_not_collapsed():
    row={'direction':'LONG_BIAS','setup':'PULLBACK','feature_payload':{
      'aiScore':80,'probability':70,'finalProbability':74,'qualityScore':77,'expectedValuePct':3.2,'rr':2.5,
      'confidence':60,'uncertainty':30,'quoteVolume':200_000_000,'marketRegime':'bull_trend',
      'aiFactors':{'trend':80,'momentum':75,'volume':70,'volatility':55,'sentiment':62,'funding':58,'open_interest':64,'liquidations':66,'alignment':78,'risk_reward':82,'capital_flow':68,'smart_money':71,'narrative':60},
      'timeframes':{'1d':'UP','4h':'UP','1h':'UP','15m':'UP','5m':'DOWN'},'marketExchanges':['binance','bybit','okx']}}
    x=extract(row)
    assert len(x)==len(FEATURE_NAMES)
    assert x[FEATURE_NAMES.index('trend')]==80
    assert x[FEATURE_NAMES.index('feature_coverage')] > .9
    assert x[FEATURE_NAMES.index('final_probability')]==74


def test_v55_legacy_neutral_fallbacks_are_marked_missing():
    row={'feature_payload':{'aiFactors':{'open_interest':70,'smart_money':50,'capital_flow':50,'narrative':50}}}
    x=extract(row)
    assert x[FEATURE_NAMES.index('missing_open_interest')]==1
    assert x[FEATURE_NAMES.index('missing_smart_money')]==1


def test_v55_same_candle_uses_1m_refinement():
    t=datetime(2026,8,1,tzinfo=timezone.utc)
    row={'direction':'LONG_BIAS','actual_entry':100,'stop':98,'tp1':104,'filled_at':t.isoformat()}
    bar=_candle(t,105,97,103)
    mins=[_candle(t,104.5,99,104,1)]
    r=_resolve(row,[bar],lambda a,b:mins)
    assert r['outcome']=='TP1'
    assert r['ambiguous_same_candle'] is True


def test_v55_unresolved_same_candle_is_not_forced_loss():
    t=datetime(2026,8,1,tzinfo=timezone.utc)
    row={'direction':'LONG_BIAS','actual_entry':100,'stop':98,'tp1':104,'filled_at':t.isoformat()}
    r=_resolve(row,[_candle(t,105,97,103)],lambda a,b:[])
    assert r['outcome']=='AMBIGUOUS'


def test_v55_purged_split_has_embargo(monkeypatch):
    monkeypatch.setenv('EXECUTION_ML_EMBARGO_HOURS','24')
    rows=[]
    for i in range(200):
        dt=datetime(2026,1,1,tzinfo=timezone.utc).timestamp()+i*12*3600
        iso=datetime.fromtimestamp(dt,tz=timezone.utc).isoformat()
        rows.append({'signal_created_at':iso,'exit_at':iso})
    tr,ca,te=_purged_split(rows)
    assert tr and ca and te
    assert datetime.fromisoformat(ca[0]['signal_created_at'])-datetime.fromisoformat(tr[-1]['signal_created_at']).replace(tzinfo=timezone.utc) >= __import__('datetime').timedelta(hours=24)


def test_v55_profit_profile_counts_shadow_execution():
    from build_profit_profile import build
    obs=[{'fingerprint':'x','symbol':'ABCUSDT','signal_direction':'LONG_BIAS','signal_score':70,'signal_created_at':'2026-08-01T00:00:00+00:00','features':json.dumps({'fingerprint':'x','direction':'LONG_BIAS','setup':'PULLBACK','aiFactors':{'trend':70},'timeframes':{}}),'real_result':json.dumps({'horizon':'24h','return_percent':5})}]
    replay=[{'sample_id':'shadow:1','sample_type':'SHADOW_EXECUTION','fingerprint':'x','symbol':'ABCUSDT','direction':'LONG_BIAS','setup':'PULLBACK','signal_created_at':'2026-08-01T00:00:00+00:00','entry_status':'filled','outcome':'SL','net_return_pct':-2,'r_multiple':-1,'feature_payload':{'fingerprint':'x','direction':'LONG_BIAS','setup':'PULLBACK','aiFactors':{'trend':70}}}]
    p=build(obs,windows=[21],replay_rows=replay)
    assert p['target_source_counts']['shadow_execution']==1
    assert p['overall']['execution_samples']==1
    assert p['overall']['win_rate']==0


def test_v55_empirical_fill_prior_is_never_implicit_100(monkeypatch):
    import execution_model_v55 as m
    class Dummy:
        status='x'
        def predict_proba(self,x): return [[.3,.7]]
    bundle={'status':'champion','version':'x','models':{'GLOBAL':{'key':'GLOBAL','fill':[],'outcome':[{'runtime_ok':True,'classifier':Dummy(),'calibrator':None,'regressor':None,'feature_indices':[0],'auc':.6}],'fill_prior':.68}}}
    monkeypatch.setattr(m,'_load_bundle',lambda:bundle)
    result=m.predict({'score':50})
    assert result['available']
    assert result['fillProbability']==68.0
    assert result['fillProbabilitySource']=='empirical_prior'
