import ai_hedge_fund_engine as hedge


def _healthy_profile():
    stat={'samples':300,'win_rate':62.0,'robust_avg_return':2.0,'robust_profit_factor':1.6,'execution_samples':60,'execution_win_rate':58.0,'execution_robust_avg_return':1.0,'execution_robust_profit_factor':1.3}
    return {
      'schema_version':53,'version':'profit-profile-v53-test','target_type':'execution_first_v53','valid':True,'validation_reasons':[],
      'overall':dict(stat),'groups':{
        'setup_direction':{'PULLBACK|LONG':dict(stat),'BREAKOUT|LONG':{**stat,'robust_avg_return':-2,'robust_profit_factor':0.6,'execution_robust_avg_return':-1,'execution_robust_profit_factor':0.5}},
        'regime_direction':{'bull_trend|LONG':dict(stat)},'structure1h_direction':{'BOS_UP|LONG':dict(stat)},'tf4h_direction':{'UP|LONG':dict(stat)},
      },'recent_windows':{'21':{}},
      'recent_overall':{'21':{'samples':100,'win_rate':60,'robust_avg_return':1.5,'robust_profit_factor':1.4,'probability_auc':0.61,'probability_brier':0.20,'execution_samples':50,'execution_probability_auc':0.58,'execution_probability_brier':0.22,'execution_robust_avg_return':0.7,'execution_robust_profit_factor':1.2}},
      'recent_rule_diagnostics':{},'rule_diagnostics':[]
    }


def base():
    return {
      'symbol':'BTCUSDT','direction':'LONG_BIAS','setup':'PULLBACK','score':88,'aiScore':82,
      'probability':82,'confidence':70,'uncertainty':30,'rr':3.0,'entryPrice':100,'stop':98,'tp1':106,
      'quoteVolume':500_000_000,'structure1h':'BOS_UP','marketRegime':'bull_trend',
      'timeframes':{'1d':'UP','4h':'UP','1h':'UP','15m':'UP','5m':'UP'},'marketExchanges':['binance','bybit','okx','mexc'],'exchangeCount':4,
      'aiFactors':{'trend':90,'volume':75,'alignment':90,'capital_flow':80,'smart_money':70,'momentum':82,'funding':75,'risk_reward':90,'open_interest':62}
    }


def test_strong_signal_passes(monkeypatch):
    monkeypatch.setattr(hedge,'_CACHE',_healthy_profile())
    monkeypatch.setattr(hedge,'_execution_calibration',lambda s:{'available':False,'samples':0})
    r=hedge.evaluate_signal(base())
    assert r['expectedValuePct'] > 0
    assert r['qualityScore'] >= 70
    assert r['qualityPassed']
    assert r['reliability']['score'] >= 90


def test_invalid_legacy_profile_fails_closed(monkeypatch):
    monkeypatch.setattr(hedge,'_CACHE',{'version':'profit-profile-v2-1144','valid':False,'validation_reasons':['legacy_schema'],'overall':{},'groups':{}})
    r=hedge.evaluate_signal(base())
    assert not r['qualityPassed']
    assert not r['profileValid']


def test_low_reliability_blocks(monkeypatch):
    monkeypatch.setattr(hedge,'_CACHE',_healthy_profile())
    monkeypatch.setattr(hedge,'_execution_calibration',lambda s:{'available':False,'samples':0})
    s=base(); s['marketExchanges']=[]; s['exchangeCount']=0; s['aiFactors'].pop('momentum'); s['aiFactors']['open_interest']=70
    r=hedge.evaluate_signal(s)
    assert not r['qualityPassed']
    assert r['reliability']['score'] < r['effectiveThresholds']['reliability']


def test_unprofitable_breakout_is_shadow_blocked(monkeypatch):
    monkeypatch.setattr(hedge,'_CACHE',_healthy_profile())
    monkeypatch.setattr(hedge,'_execution_calibration',lambda s:{'available':False,'samples':0})
    s=base(); s['setup']='BREAKOUT'
    r=hedge.evaluate_signal(s)
    assert r['breakoutGuard']['blocked']
    assert not r['qualityPassed']


def test_probability_not_double_counted_through_ev(monkeypatch):
    monkeypatch.setattr(hedge,'_CACHE',_healthy_profile())
    monkeypatch.setattr(hedge,'_execution_calibration',lambda s:{'available':False,'samples':0})
    s1=base(); s2=base(); s2['tp1']=112  # changes EV geometry, not independent Quality inputs
    r1=hedge.evaluate_signal(s1); r2=hedge.evaluate_signal(s2)
    assert r2['expectedValuePct'] > r1['expectedValuePct']
    assert r2['qualityScore'] == r1['qualityScore']
