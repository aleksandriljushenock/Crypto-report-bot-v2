from signal_quality_engine import evaluate_signal_quality


def base_signal():
    return {
        "direction": "LONG_BIAS", "setup": "PULLBACK", "score": 89,
        "probability": 74, "confidence": 58, "uncertainty": 42,
        "aiScore": 76, "rr": 3.0, "quoteVolume": 500_000_000,
        "alignment": 88, "timeframes": {"1d": "UP", "4h": "UP", "1h": "UP", "15m": "UP", "5m": "UP"},
        "structure1h": "BOS_UP", "structure15m": "BOS_UP",
        "aiFactors": {"trend": 90, "volume": 72, "capital_flow": 78, "smart_money": 72},
    }


def test_strong_profile_passes():
    result = evaluate_signal_quality(base_signal())
    assert result["qualityPassed"] is True
    assert result["qualityScore"] >= 72


def test_four_hour_conflict_blocks_long():
    signal = base_signal()
    signal["timeframes"]["4h"] = "DOWN"
    result = evaluate_signal_quality(signal)
    assert result["qualityPassed"] is False
    assert "four_hour_conflict" in result["qualityHardBlocks"]


def test_weak_breakout_is_rejected():
    signal = base_signal()
    signal.update({"setup": "BREAKOUT", "quoteVolume": 50_000_000, "probability": 55, "confidence": 32, "uncertainty": 70})
    signal["aiFactors"].update({"volume": 42, "capital_flow": 45, "smart_money": 40})
    result = evaluate_signal_quality(signal)
    assert result["qualityPassed"] is False
