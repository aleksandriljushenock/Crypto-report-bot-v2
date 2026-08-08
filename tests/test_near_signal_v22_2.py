import importlib


def test_only_one_close_gate_is_near(monkeypatch, tmp_path):
    import near_signal_watchlist as n
    n.DB_PATH = tmp_path / 'near.db'
    monkeypatch.setenv('NEAR_SIGNAL_MIN_DISTANCE_PCT', '85')
    good = {
        'symbol':'XRPUSDT','reason':'Probability','score':80,'rr':2.5,'probability':65,
        'profileThresholds':{'score':72,'rr':2.2,'probability':69},
    }
    c=n.classify_near_candidate(good)
    assert c and c['nearMissingGate']=='Probability' and c['nearDistanceScore'] > 94

    assert n.classify_near_candidate({
        'symbol':'ZECUSDT','reason':'Quality, EV, anti-profile','qualityScore':52.2,
        'expectedValuePct':0.16,'profileQualityThreshold':70,'profileEvThreshold':2.0,
    }) is None


def test_unknown_ai_metrics_are_not_zero(monkeypatch, tmp_path):
    import near_signal_watchlist as n
    n.DB_PATH = tmp_path / 'near.db'
    monkeypatch.setenv('NEAR_SIGNAL_MIN_DISTANCE_PCT', '85')
    item={'symbol':'ETHUSDT','reason':'R/R','score':85,'rr':2.05,'probability':77,
          'profileThresholds':{'score':72,'rr':2.2,'probability':69}}
    assert n.upsert_near_candidates([item]) == 1
    row=n.get_rows()[0]
    assert row['quality'] is None
    assert row['ev'] is None
    assert row['missing_gate']=='R/R'


def test_far_single_gate_goes_not_near(monkeypatch):
    import near_signal_watchlist as n
    monkeypatch.setenv('NEAR_SIGNAL_MIN_DISTANCE_PCT', '85')
    item={'symbol':'DOGEUSDT','reason':'Quality','qualityScore':44.1,'profileQualityThreshold':70}
    assert n.classify_near_candidate(item) is None
