from chronos_forecaster import blend_signal


def test_blend_long_signal():
    signal = {
        "direction": "LONG_BIAS",
        "probability": 70,
        "confidence": 68,
        "tradeProfile": {},
    }
    forecast = {
        "probabilityUp": 75,
        "probabilityDown": 25,
        "forecastReturnPct": 1.2,
        "uncertaintyPct": 1.5,
    }
    result = blend_signal(signal, forecast)
    assert 70 <= result["probability"] <= 75
    assert result["chronos"]["directionAgreement"] is True
    assert result["tradeProfile"]["chronos"] == 75


def test_blend_short_signal():
    signal = {
        "direction": "SHORT_BIAS",
        "probability": 70,
        "confidence": 68,
        "tradeProfile": {},
    }
    forecast = {
        "probabilityUp": 30,
        "probabilityDown": 70,
        "forecastReturnPct": -1.0,
        "uncertaintyPct": 1.0,
    }
    result = blend_signal(signal, forecast)
    assert result["chronos"]["directionAgreement"] is True
