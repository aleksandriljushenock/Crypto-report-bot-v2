from strategies.service import _compute_stats_from_rows


def _row(state, ret, created):
    return {"state": state, "return_pct": ret, "created_at": created}


def test_stats_use_full_forward_outcomes_and_breakeven():
    rows = [
        _row("won", 10.0, "2026-01-01T00:00:00+00:00"),
        _row("lost", -5.0, "2026-01-02T00:00:00+00:00"),
        _row("breakeven", 0.0, "2026-01-03T00:00:00+00:00"),
        _row("waiting_entry", None, "2026-01-04T00:00:00+00:00"),
        _row("open", None, "2026-01-05T00:00:00+00:00"),
        _row("expired", 0.0, "2026-01-06T00:00:00+00:00"),
    ]
    s = _compute_stats_from_rows("demo", rows)
    assert s["total"] == 6
    assert s["resolved"] == 3
    assert s["wins"] == 1 and s["losses"] == 1 and s["breakeven"] == 1
    assert s["win_rate"] == 50.0
    assert s["profit_factor"] == 2.0
    assert s["waiting"] == 1 and s["open"] == 1 and s["expired"] == 1
    assert round(s["compounded_return"], 2) == 4.5
    assert s["max_drawdown"] < 0


def test_stats_drawdown_is_equity_percentage_not_percentage_point_sum():
    rows = [
        _row("won", 100.0, "2026-01-01T00:00:00+00:00"),
        _row("lost", -50.0, "2026-01-02T00:00:00+00:00"),
    ]
    s = _compute_stats_from_rows("demo", rows)
    # 100 -> 200 -> 100, so drawdown from peak is -50%, not -50 percentage-points by coincidence only.
    assert round(s["max_drawdown"], 2) == -50.0
    assert round(s["compounded_return"], 2) == 0.0
