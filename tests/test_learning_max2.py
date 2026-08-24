from __future__ import annotations


def test_learning_max2_smoke(tmp_path, monkeypatch):
    import learning_max2 as lm
    import smart_money_engine
    import news_intelligence

    monkeypatch.setattr(lm, 'DB_PATH', tmp_path / 'learning_max2.db')
    factors = {
        'trend':72,'momentum':68,'volume':61,'funding':55,'open_interest':70,
        'alignment':75,'risk_reward':64,'capital_flow':66,'narrative':58,
        'news':62,'smart_money':71,
    }
    pred = lm.predict(factors, 'LONG_BIAS')
    exp = lm.explain(factors, pred)
    assert 0 <= pred['probability'] <= 100
    assert len(pred['specialists']) == 6
    assert 'weighted_v14_score' in pred
    assert exp['strong_factors'] and exp['why_enter']

    sm = smart_money_engine.calculate_smart_money_score({
        'whale_alert':80,'exchange_netflow':70,'etf_flow':65,'stablecoin_flow':60,
        'funding':55,'open_interest':72,'liquidations':68,
    })
    assert sm['smart_money_score'] > 50

    news = news_intelligence.enrich([
        {'title':'SEC approves Bitcoin ETF inflow'},
        {'title':'SEC approves Bitcoin ETF inflow'},
        {'title':'Exchange hack exploit'},
    ])
    assert len(news) == 2
    assert news_intelligence.clusters(news)

    lm.save_observation({'fingerprint':'smoke-test','symbol':'BTCUSDT','direction':'LONG_BIAS','aiFactors':factors,**pred})
    assert lm.status()['stored_signals'] >= 1
