from pathlib import Path


def test_profile_selection(monkeypatch):
    import trade_engine
    monkeypatch.setenv('MOMENTUM_PROFILE_MIN_CHANGE_PCT','4')
    monkeypatch.setenv('MOMENTUM_PROFILE_MIN_REL_VOLUME','1.25')
    assert trade_engine._strategy_profile({'setup':'PULLBACK','priceChange24h':1,'relativeVolume15m':1.1}) == 'PULLBACK'
    assert trade_engine._strategy_profile({'setup':'BREAKOUT','priceChange24h':1,'relativeVolume15m':1.1}) == 'BREAKOUT'
    assert trade_engine._strategy_profile({'setup':'BREAKOUT','priceChange24h':6,'relativeVolume15m':1.5}) == 'MOMENTUM'


def test_profile_threshold_overrides(monkeypatch):
    import trade_engine
    monkeypatch.setenv('TRADE_PULLBACK_MIN_SCORE','71')
    monkeypatch.setenv('TRADE_PULLBACK_MIN_RR','2.1')
    monkeypatch.setenv('TRADE_PULLBACK_MIN_PROBABILITY','67')
    t=trade_engine._profile_thresholds('PULLBACK',72,2.3,70)
    assert t == {'score':71.0,'rr':2.1,'probability':67.0}


def test_near_signal_watchlist(monkeypatch,tmp_path):
    import near_signal_watchlist as n
    n.DB_PATH = tmp_path/'near.db'
    monkeypatch.setenv('NEAR_SIGNAL_RESCAN_MINUTES','1')
    n.upsert_near_candidates([{'symbol':'ABCUSDT','reason':'EV','probability':72,'qualityScore':75,'expectedValuePct':1.9,'score':80}])
    rows=n.get_rows()
    assert rows and rows[0]['symbol']=='ABCUSDT'


def test_shadow_register_no_paper(monkeypatch,tmp_path):
    import shadow_signals as s
    s.DB_PATH = tmp_path/'shadow.db'
    s._RESTORED = True
    monkeypatch.setenv('SHADOW_CLOUD_ENABLED','false')
    count=s.register_shadow_candidates([{'symbol':'ABCUSDT','direction':'LONG_BIAS','setup':'PULLBACK','reason':'Quality','entryPrice':10,'stop':9,'tp1':12,'probability':70,'qualityScore':69}], source='test')
    assert count == 1
    with s._conn() as c:
        row=c.execute('select * from shadow_signals').fetchone()
    assert row['status']=='pending_entry'
    assert row['actual_entry'] is None
