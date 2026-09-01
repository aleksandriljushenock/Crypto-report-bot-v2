from datetime import datetime, timedelta, timezone
import os

import backfill_execution_dataset_v56 as bf
import execution_model_v56 as em


def _rows(n=400):
    out=[]
    base=datetime(2026,1,1,tzinfo=timezone.utc)
    for i in range(n):
        t=base+timedelta(hours=12*i)
        out.append({'signal_created_at':t.isoformat(),'exit_at':(t+timedelta(hours=2)).isoformat()})
    return out


def test_v56_non_binance_5m_close_time_is_five_minutes():
    o=1_700_000_000_000
    bybit={'result':{'list':[[str(o),'1','2','0.5','1.5','0','0']]}}
    row=bf._normalize('bybit',bybit,'5m')[0]
    assert row[4]-row[0] == 300_000
    row1=bf._normalize('bybit',bybit,'1m')[0]
    assert row1[4]-row1[0] == 60_000


def test_v56_okx_5m_close_time_is_five_minutes():
    o=1_700_000_000_000
    row=bf._normalize('okx',{'data':[[str(o),'1','2','0.5','1.5']]},'5m')[0]
    assert row[4]-row[0] == 300_000


def test_v56_fetch_chunks_72h_for_okx(monkeypatch):
    calls=[]
    def fake(session,url,params):
        calls.append(params.copy())
        # one candle in each chunk is enough for coverage check
        ts=int(params['before'])
        return {'data':[[str(ts),'1','2','0.5','1.5']]}
    monkeypatch.setattr(bf,'_request_json',fake)
    start=datetime(2026,1,1,tzinfo=timezone.utc); end=start+timedelta(hours=72)
    rows,error=bf._fetch('okx','BTCUSDT',start,end,'5m',session=object())
    assert error is None
    assert len(calls) >= 3  # 25h max per 300 x 5m page
    assert all(int(c['after']) > int(c['before']) for c in calls)


def test_v56_provider_attempt_records_error(monkeypatch):
    monkeypatch.setattr(bf,'PROVIDERS',['binance'])
    monkeypatch.setattr(bf,'_fetch',lambda *a,**k:([], 'HTTPError: 429'))
    rows,provider,attempts=bf._candles_any('BTCUSDT',datetime.now(timezone.utc),datetime.now(timezone.utc)+timedelta(hours=1))
    assert rows==[] and provider is None
    assert attempts[0]['error']=='HTTPError: 429'


def test_v56_split_has_untouched_champion_window(monkeypatch):
    monkeypatch.setenv('EXECUTION_ML_EMBARGO_HOURS','24')
    tr,ca,se,ch=em._purged_split(_rows())
    assert tr and ca and se and ch
    assert datetime.fromisoformat(ch[0]['signal_created_at']) > datetime.fromisoformat(se[-1]['signal_created_at'])


def test_v56_weighted_ensemble_does_not_reward_model_count():
    class C:
        def __init__(self,p): self.p=p
        def predict_proba(self,x): return [[1-self.p,self.p]]
    x=[0.0]*len(em.FEATURE_NAMES)
    good={'runtime_ok':True,'champion_ok':True,'classifier':C(.8),'calibrator':None,'regressor':None,'feature_indices':[0],'champion_auc':.70,'champion_brier':.15}
    weak={'runtime_ok':True,'champion_ok':True,'classifier':C(.2),'calibrator':None,'regressor':None,'feature_indices':[0],'champion_auc':.60,'champion_brier':.23}
    r=em._ensemble([good,weak],x,require_champion=True)
    assert r['probability'] > 50  # better OOS model gets more influence


def test_v56_safe_runtime_defaults_are_consistent(monkeypatch):
    import strategy_settings as ss
    by={s.key:s.default for s in ss.SPECS}
    assert by['ADAPTIVE_MODEL_MIN_TRADES']==150
    assert by['ADAPTIVE_MODEL_MIN_VALIDATION']==30
    assert by['ADAPTIVE_MODEL_BLEND_WEIGHT']==0.10
    assert by['AI_OPTIMIZER_MIN_TRADES']==150
    assert by['MULTI_EXCHANGE_MIN_VENUES']==2
