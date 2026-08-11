from strategies.catalog import get_strategy
from strategies.analyzers import analyze_strategy
from strategies.scheduler import status


def _rows(count, start=100.0, step=0.15, volume=1_000_000.0):
    out=[]; price=start
    for i in range(count):
        o=price; c=price+step
        h=max(o,c)+0.5; l=min(o,c)-0.5
        out.append([1_700_000_000_000+i*3_600_000,o,h,l,c,volume+(i%5)*100_000])
        price=c
    return out


def test_smc_catalog_and_analyzer_smoke():
    spec=get_strategy("smc")
    assert spec.key == "smart_money_confluence"
    assert spec.needs_h1 is True
    d1=_rows(260,100,0.25)
    h4=_rows(240,140,0.10)
    h1=_rows(300,160,0.03)
    deriv={
        "h1_rows":h1,
        "premium":{"lastFundingRate":"-0.0001"},
        "oi_history":[{"sumOpenInterestValue":str(100+i)} for i in range(24)],
    }
    result=analyze_strategy(spec.key,"TESTUSDT",300_000_000,d1,h4,"test",deriv)
    assert result["symbol"] == "TESTUSDT"
    assert result.get("status") in {"READY","WATCH","WAITING","NO_SETUP"}


def test_scheduler_defaults_are_safe(monkeypatch):
    monkeypatch.setenv("STRATEGY_LAB_AUTO_ENABLED","true")
    monkeypatch.setenv("STRATEGY_LAB_AUTO_INTERVAL_MINUTES","30")
    monkeypatch.setenv("STRATEGY_LAB_AUTO_MODE","round_robin")
    st=status()
    assert st["enabled"] is True
    assert st["interval_minutes"] == 30
    assert st["mode"] == "round_robin"
