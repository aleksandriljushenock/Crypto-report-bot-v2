from strategies.fib_pullback import analyze_symbol, atr, normalize_klines
from telegram_ui.keyboards import main_keyboard, strategies_keyboard, fib_strategy_keyboard


def _row(ts, o, h, l, c, v=1000):
    return [ts, str(o), str(h), str(l), str(c), str(v), ts, 0, 0, 0, 0, 0]


def test_strategy_menu_is_exposed():
    main = str(main_keyboard())
    assert "menu_strategies" in main
    assert "strategy_fib05" in str(strategies_keyboard())
    fib = str(fib_strategy_keyboard())
    assert "fib05_scan" in fib
    assert "fib05_winrate" in fib
    assert "fib05_candidates" in fib


def test_normalize_and_atr():
    rows = [_row(i * 86400000, 100+i, 102+i, 99+i, 101+i) for i in range(20)]
    candles = normalize_klines(rows)
    assert len(candles) == 20
    assert atr(candles, 14) > 0


def test_no_setup_on_insufficient_data():
    rows = [_row(i * 86400000, 100, 101, 99, 100) for i in range(10)]
    result = analyze_symbol("TESTUSDT", 200_000_000, rows, rows)
    assert result["status"] == "NO_SETUP"
