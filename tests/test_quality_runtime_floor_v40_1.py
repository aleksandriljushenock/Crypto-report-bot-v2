import os
import ai_intelligence


def _signal(quality, profile='BREAKOUT', ev=5.0):
    return {
        'symbol': 'TESTUSDT',
        'signalProfile': profile,
        'setup': profile,
        'qualityScore': quality,
        'expectedValuePct': ev,
        'aiScore': 80,
        'score': 80,
        'probability': 80,
        'qualityRules': [],
    }


def test_global_quality_is_hard_floor(monkeypatch):
    monkeypatch.setenv('HEDGE_QUALITY_GATE_ENABLED', 'true')
    monkeypatch.setenv('HEDGE_MIN_QUALITY', '75')
    monkeypatch.setenv('HEDGE_BREAKOUT_MIN_QUALITY', '72')
    monkeypatch.setenv('HEDGE_MIN_EV_PCT', '0')
    monkeypatch.setenv('HEDGE_BREAKOUT_MIN_EV_PCT', '0')
    monkeypatch.setattr(ai_intelligence, 'enrich_signal', lambda s: dict(s))
    monkeypatch.setattr(ai_intelligence, 'save_ai_score', lambda s: None)

    # Avoid dependencies on the hedge/Chronos engines in this threshold test.
    import sys, types
    hedge = types.SimpleNamespace(evaluate_signal=lambda s: {})
    chronos = types.SimpleNamespace(apply_to_finalists=lambda xs: xs)
    monkeypatch.setitem(sys.modules, 'ai_hedge_fund_engine', hedge)
    monkeypatch.setitem(sys.modules, 'chronos_forecaster', chronos)

    ranked = ai_intelligence.rank_signals([_signal(73)])
    assert ranked == []
    diag = ai_intelligence.get_last_rank_diagnostics()
    assert diag['rejected'][0]['profileQualityThreshold'] == 75.0


def test_profile_threshold_can_be_stricter(monkeypatch):
    monkeypatch.setenv('HEDGE_QUALITY_GATE_ENABLED', 'true')
    monkeypatch.setenv('HEDGE_MIN_QUALITY', '75')
    monkeypatch.setenv('HEDGE_BREAKOUT_MIN_QUALITY', '80')
    monkeypatch.setenv('HEDGE_MIN_EV_PCT', '0')
    monkeypatch.setenv('HEDGE_BREAKOUT_MIN_EV_PCT', '0')
    monkeypatch.setattr(ai_intelligence, 'enrich_signal', lambda s: dict(s))
    monkeypatch.setattr(ai_intelligence, 'save_ai_score', lambda s: None)

    import sys, types
    monkeypatch.setitem(sys.modules, 'ai_hedge_fund_engine', types.SimpleNamespace(evaluate_signal=lambda s: {}))
    monkeypatch.setitem(sys.modules, 'chronos_forecaster', types.SimpleNamespace(apply_to_finalists=lambda xs: xs))

    assert ai_intelligence.rank_signals([_signal(78)]) == []
    ranked = ai_intelligence.rank_signals([_signal(81)])
    assert len(ranked) == 1
    assert ranked[0]['profileQualityThreshold'] == 80.0
