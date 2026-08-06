from ai_hedge_fund_engine import evaluate_signal

def base():
    return {
      'symbol':'BTCUSDT','direction':'LONG_BIAS','setup':'PULLBACK','score':88,'aiScore':78,
      'probability':74,'confidence':55,'uncertainty':45,'rr':3.0,'entryPrice':100,'stop':98,'tp1':106,
      'quoteVolume':500_000_000,'structure1h':'BOS_UP','marketRegime':'bull_trend',
      'timeframes':{'1d':'UP','4h':'UP','1h':'UP','15m':'UP','5m':'UP'},
      'aiFactors':{'trend':90,'volume':75,'alignment':90,'capital_flow':80,'smart_money':70}
    }

def test_strong_signal_passes():
    r=evaluate_signal(base())
    assert r['expectedValuePct'] > 0
    assert r['qualityScore'] >= 70
    assert r['qualityPassed']

def test_anti_profile_blocks():
    s=base(); s.update({'quoteVolume':20_000_000,'confidence':20,'uncertainty':80}); s['timeframes']['4h']='DOWN'; s['aiFactors']['capital_flow']=35
    r=evaluate_signal(s)
    assert not r['qualityPassed']
    assert 'long_against_4h' in r['antiProfileHits']
