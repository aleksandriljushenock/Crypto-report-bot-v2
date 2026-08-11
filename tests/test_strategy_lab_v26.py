from strategies.catalog import STRATEGIES, get_strategy
from strategies.analyzers import analyze_strategy
from strategies.service import _entry_touched, _bar_resolution, _return_pct
from strategies.reports import strategy_home_text, strategy_detail_text, rules_text
from telegram_ui.keyboards import strategies_keyboard, strategy_lab_keyboard


def _rows(count, start=100.0, step=0.35, volume=1_000_000.0):
    out=[]
    price=start
    for i in range(count):
        o=price
        c=price+step
        h=max(o,c)+0.6
        l=min(o,c)-0.6
        out.append([1_700_000_000_000+i*14_400_000,o,h,l,c,volume+(i%7)*50_000])
        price=c
    return out


def test_catalog_contains_all_strategy_lab_hypotheses():
    assert len(STRATEGIES) == 12
    keys={s.key for s in STRATEGIES}
    assert {
        "fib_05_pullback","smart_money_confluence","liquidity_sweep_reclaim","ema_trend_pullback","breakout_retest",
        "range_mean_reversion","anchored_vwap_pullback","volatility_squeeze","donchian_trend",
        "funding_oi_squeeze","oi_price_divergence","rsi_divergence_structure",
    } == keys
    assert get_strategy("sweep").key == "liquidity_sweep_reclaim"


def test_strategy_menu_exposes_all_strategies_and_leaderboard():
    text=str(strategies_keyboard())
    assert "strategy_leaderboard" in text
    for spec in STRATEGIES:
        assert f"strategy_{spec.short}" in text
        detail=str(strategy_lab_keyboard(spec.key))
        assert f"lab_{spec.short}_scan" in detail
        assert f"lab_{spec.short}_winrate" in detail
        assert f"lab_{spec.short}_candidates" in detail
        assert f"lab_{spec.short}_history" in detail
        assert f"lab_{spec.short}_outcomes" in detail
        assert f"lab_{spec.short}_rules" in detail


def test_all_strategy_analyzers_smoke_without_network():
    d1=_rows(260,100,0.4)
    h4=_rows(240,150,0.12)
    deriv={
        "premium":{"lastFundingRate":"-0.0005"},
        "oi_history":[{"sumOpenInterestValue":str(100+i*2)} for i in range(24)],
    }
    for spec in STRATEGIES:
        result=analyze_strategy(spec.key,"TESTUSDT",200_000_000,d1,h4,"test",deriv)
        assert result["symbol"] == "TESTUSDT"
        assert result.get("status") in {"READY","WATCH","WAITING","NO_SETUP"}


def test_directional_forward_execution_helpers_are_conservative():
    long_bar={"low":99.0,"high":106.0}
    short_bar={"low":94.0,"high":101.0}
    assert _entry_touched(long_bar,"LONG",100,"LIMIT")
    assert _entry_touched(long_bar,"LONG",105,"STOP")
    assert _entry_touched(short_bar,"SHORT",100,"LIMIT")
    assert _entry_touched(short_bar,"SHORT",95,"STOP")
    assert _bar_resolution({"low":89,"high":111},"LONG",90,110) == ("lost","SL_AMBIGUOUS")
    assert _bar_resolution({"low":89,"high":111},"SHORT",110,90) == ("lost","SL_AMBIGUOUS")
    assert _return_pct("LONG",100,110) > 0
    assert _return_pct("SHORT",100,90) > 0
    assert _return_pct("LONG",100,90) < 0
    assert _return_pct("SHORT",100,110) < 0


def test_strategy_reports_are_generated_for_every_strategy():
    home=strategy_home_text()
    assert "12" in home
    for spec in STRATEGIES:
        assert spec.title.upper() in strategy_detail_text(spec.key)
        rules=rules_text(spec.key)
        assert "Forward-tracking" in rules
