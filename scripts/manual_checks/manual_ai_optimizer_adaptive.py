import math

from ai_optimizer import _metric
from adaptive_model_manager import _standardize, _train, _predict


def test_optimizer_metric():
    rows = [{"net_pnl": 1.0}, {"net_pnl": -0.5}, {"net_pnl": 2.0}]
    m = _metric(rows)
    assert m["trades"] == 3
    assert m["wins"] == 2
    assert round(m["pnl"], 6) == 2.5
    assert m["profit_factor"] == 6.0


def test_pure_python_logistic_learns_direction():
    raw = [[10.0] + [0.0]*11, [20.0] + [0.0]*11, [80.0] + [0.0]*11, [90.0] + [0.0]*11]
    ys = [0, 0, 1, 1]
    zx, means, stds = _standardize(raw)
    w, b = _train(zx, ys)
    model = {"means": means, "stds": stds, "weights": w, "bias": b}
    assert _predict(raw[-1], model) > _predict(raw[0], model)
    assert 0 <= _predict(raw[-1], model) <= 1
