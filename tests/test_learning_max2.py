from learning_max2 import initialize, predict, explain, save_observation, status
from smart_money_engine import calculate_smart_money_score
from news_intelligence import enrich, clusters

def run():
    initialize()
    factors={'trend':72,'momentum':68,'volume':61,'funding':55,'open_interest':70,'alignment':75,'risk_reward':64,'capital_flow':66,'narrative':58,'news':62,'smart_money':71}
    pred=predict(factors,'LONG_BIAS'); exp=explain(factors,pred)
    assert 0 <= pred['probability'] <= 100 and len(pred['specialists']) == 6
    assert exp['strong_factors'] and exp['why_enter']
    sm=calculate_smart_money_score({'whale_alert':80,'exchange_netflow':70,'etf_flow':65,'stablecoin_flow':60,'funding':55,'open_interest':72,'liquidations':68})
    assert sm['smart_money_score'] > 50
    news=enrich([{'title':'SEC approves Bitcoin ETF inflow'},{'title':'SEC approves Bitcoin ETF inflow'},{'title':'Exchange hack exploit'}])
    assert len(news)==2 and clusters(news)
    save_observation({'fingerprint':'smoke-test','symbol':'BTCUSDT','direction':'LONG_BIAS','aiFactors':factors,**pred})
    assert status()['stored_signals'] >= 1
    print('Learning MAX 2.0 smoke test passed')
if __name__=='__main__': run()
