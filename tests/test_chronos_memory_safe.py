import os

from chronos_forecaster import apply_to_finalists, blend_signal


def test_blend_marks_applied():
    signal = {'direction': 'LONG_BIAS', 'probability': 60, 'confidence': 50}
    forecast = {'probabilityUp': 70, 'forecastReturnPct': 1.2, 'uncertaintyPct': 2.0, 'model': 'tiny'}
    result = blend_signal(signal, forecast)
    assert result['chronosStatus'] == 'applied'
    assert result['chronos']['directionAgreement'] is True


def test_disabled_removes_internal_closes(monkeypatch):
    monkeypatch.setenv('CHRONOS_ENABLED', 'false')
    rows = [{'_chronosCloses': [1, 2, 3], 'symbol': 'BTCUSDT'}]
    result = apply_to_finalists(rows)
    assert '_chronosCloses' not in result[0]
