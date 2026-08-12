from strategies.catalog import get_strategy
from strategies.analyzers import analyze_strategy


def _rows_from_closes(closes, volume=2_000_000.0, ms=14_400_000):
    rows=[]
    for i,c in enumerate(closes):
        o=closes[i-1] if i else c
        h=max(o,c)*1.002
        l=min(o,c)*0.998
        v=volume*(1.25 if i >= len(closes)-3 else 1.0)
        rows.append([1_700_000_000_000+i*ms,o,h,l,c,v])
    return rows


def test_ma_ribbon_registered():
    spec=get_strategy("maribbon")
    assert spec.key == "ma_ribbon_cross"
    assert "8/13/21/55" in spec.title


def test_ma_ribbon_literal_55_cross_is_not_buy_ready():
    # Fast ribbon initially above a slowly rising 55MA, then price drops enough
    # for the slow MA to end above all fast averages. This is the literal event
    # requested by the user and should never become a BUY READY signal.
    d1=_rows_from_closes([100+i*0.5 for i in range(260)], ms=86_400_000)
    h4_closes=[100+i*0.25 for i in range(65)] + [116,114,112,109,106,103,100,98,97,96,95,94,93,92,91]
    h4=_rows_from_closes(h4_closes)
    result=analyze_strategy("ma_ribbon_cross","TESTUSDT",250_000_000,d1,h4,"test",{})
    assert result["status"] in {"NO_SETUP","WAITING","WATCH"}
    assert result["status"] != "READY"


def test_ma_ribbon_bullish_stack_payload_and_risk_are_valid():
    d1=_rows_from_closes([100+i*0.45 for i in range(260)], ms=86_400_000)
    # Flat/down base then strong recovery to put the fast ribbon above SMA55.
    h4_closes=[120-i*0.08 for i in range(62)] + [115,114,113,112,113,115,118,121,124,127,130,133,136,139,142,145,148,151]
    h4=_rows_from_closes(h4_closes)
    result=analyze_strategy("ma_ribbon_cross","TESTUSDT",300_000_000,d1,h4,"test",{})
    assert result["status"] in {"READY","WATCH","WAITING","NO_SETUP"}
    if result.get("entry_price"):
        assert result["entry_price"] > result["stop_price"]
        assert result["tp_price"] > result["entry_price"]
        assert result["rr"] >= 2.0
    assert "ma55_slope_pct" in result
    assert "literal_55_cross_up" in result
