from analyzer import build_trade_levels
from build_profit_profile import build, validate_profile, FACTORS
from rule_engine import evaluate_rules


def _kline(i, close):
    return [i, close, close*1.01, close*0.99, close, 1000, 0, 100000, 100, 500, 500, 0]


def test_short_trade_levels_are_mirrored():
    c15=[_kline(i,100+i*0.02) for i in range(60)]
    c1h=[_kline(i,100+i*0.05) for i in range(60)]
    data={'klines':{'15m':c15,'1h':c1h}}
    long=build_trade_levels(data,'LONG_BIAS'); short=build_trade_levels(data,'SHORT_BIAS')
    assert long['breakoutStop'] < long['breakoutEntry'] < long['tp2']
    assert short['tp2'] < short['breakoutEntry'] < short['breakoutStop']
    assert short['pullbackStop'] > sum(short['pullbackEntryZone'])/2


def test_execution_target_overrides_mark_to_market():
    factors={k:60 for k in FACTORS}
    obs={'id':'1','symbol':'AAAUSDT','signal_direction':'LONG_BIAS','signal_created_at':'2026-08-01T00:00:00+00:00',
         'features':{'fingerprint':'fp1','setup':'PULLBACK','direction':'LONG_BIAS','aiFactors':factors,'timeframes':{'4h':'UP'}},
         'real_result':{'returns':{'24h':10},'success':True}}
    paper={'fingerprint':'fp1','symbol':'AAAUSDT','side':'LONG','status':'closed','entry_price':100,'exit_price':98,'stop_price':98,'net_pnl':-2,'notional_usd':100,
           'opened_at':'2026-08-01T00:10:00+00:00','closed_at':'2026-08-01T01:00:00+00:00','execution_verified':True,
           'signal_payload':{'fingerprint':'fp1','setup':'PULLBACK','direction':'LONG_BIAS','aiFactors':factors,'timeframes':{'4h':'UP'}}}
    p=build([obs],[21],execution_rows=[paper])
    assert p['target_source_counts']['paper_execution']==1
    assert p['overall']['win_rate']==0.0
    assert p['overall']['execution_win_rate']==0.0
    assert validate_profile(p)[0]


def _score(direction):
    short=direction=='SHORT_BIAS'
    return {'symbol':'AAAUSDT','quoteVolume':200_000_000,'direction':direction,'status':'TRADE_CANDIDATE',
      'structure15m':'BOS_DOWN' if short else 'BOS_UP','structure1h':'BOS_DOWN' if short else 'BOS_UP','fundingPercent':0,
      'takerBuySellRatio':0.8 if short else 1.2,'rsi1h':50,'ema20_1h':90 if short else 110,'ema50_1h':100,'ema200_1h':105 if short else 95,
      'vwap15m':100,'atr1hPercent':1.0,'relativeStrength':{'label':'WEAK' if short else 'STRONG'},'oiAnalysis':{'label':'OI_STABLE'},
      'fundingAnalysis':{'label':'FUNDING_NEUTRAL'},
      'smcAnalysis':{'15m':{'liquiditySweep':{'found':True,'type':'SWEEP_HIGH' if short else 'SWEEP_LOW'},'bias':'BEARISH' if short else 'BULLISH'},
                     '1h':{'available':True,'event':'BOS','eventDirection':'DOWN' if short else 'UP','bias':'BEARISH' if short else 'BULLISH'},
                     '4h':{'available':True,'bias':'BEARISH' if short else 'BULLISH'}},
      'orderBlockAnalysis':{'15m':{'context':'IN_BEARISH_OB' if short else 'IN_BULLISH_OB'},'1h':{'context':'BEARISH_OB_NEAREST' if short else 'BULLISH_OB_NEAREST'}},
      'fvgAnalysis':{'15m':{'context':'IN_BEARISH_FVG' if short else 'IN_BULLISH_FVG'},'1h':{'context':'BEARISH_FVG_NEAREST' if short else 'BULLISH_FVG_NEAREST'}}}


def test_short_confirmations_are_directional():
    levels={'breakoutRR':3,'pullbackRR':2.5}
    r=evaluate_rules(_score('SHORT_BIAS'),levels)
    assert r['action']=='TRADE'
    assert any('ниже EMA50' in x for x in r['confirmations'])
    assert not any('выше EMA50' in x for x in r['confirmations'])
