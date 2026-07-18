from unittest.mock import patch

import smart_money_engine as engine
import smart_money_sources as sources


def _trades(buy=True):
    return [{"p": "100", "q": "2000", "m": not buy, "T": 1} for _ in range(20)]


def test_score_coverage():
    result = engine.calculate_smart_money_score({
        "funding": {"available": True, "score": 70, "quality": "direct"},
        "whale_alert": {"available": True, "score": 60, "quality": "proxy"},
    })
    assert result["coverage"] == 28.0
    assert result["direct_coverage"] == 10.0
    assert result["smart_money_score"] > 60


def test_public_collectors_with_mocks():
    def fake_json(url, params=None, **kwargs):
        if "funding/history" in url:
            return {"result": {"list": [{"fundingRate": "-0.0002"}]}}
        if "open-interest" in url:
            return {"result": {"list": [{"openInterest": "110"}, {"openInterest": "100"}]}}
        if "recent-trade" in url:
            return {"result": {"list": [{"price": "100", "size": "2000", "side": "Buy"} for _ in range(20)]}}
        if "stablecoincharts" in url:
            return [{"totalCirculatingUSD": {"peggedUSD": 100}}, {"totalCirculatingUSD": {"peggedUSD": 102}}]
        raise AssertionError(url)

    with patch.object(sources, "_provider_order", return_value=["bybit"]), patch.object(sources.http, "get_json", side_effect=fake_json):
        assert sources.collect_funding("BTCUSDT").available
        assert sources.collect_open_interest("BTCUSDT").score > 50
        assert sources.collect_stablecoin_flow("BTCUSDT").score > 50
        assert sources.collect_whale_activity("BTCUSDT").available
        assert sources.collect_exchange_netflow("BTCUSDT").available
        assert sources.collect_liquidations("BTCUSDT").available


def test_engine_survives_source_failure():
    def fake(name, symbol):
        return {"component": name, "available": name != "etf_flow", "score": 55 if name != "etf_flow" else None, "quality": "proxy", "error": "blocked" if name == "etf_flow" else ""}

    with patch.object(engine, "safe_collect", side_effect=fake), patch.object(engine, "save_snapshot"):
        result = engine.collect_smart_money("BTCUSDT")
    assert result["coverage"] == 86.0
    assert result["sources"]["etf_flow"]["available"] is False


def test_binance_failure_falls_back_to_bybit():
    def fake_json(url, params=None, **kwargs):
        if "binance.com" in url:
            raise RuntimeError("418 blocked")
        if "recent-trade" in url:
            return {"result": {"list": [{"price": "100", "size": "2000", "side": "Buy"}]}}
        raise AssertionError(url)

    with patch.object(sources, "_provider_order", return_value=["binance", "bybit"]), patch.object(sources.http, "get_json", side_effect=fake_json):
        result = sources.collect_exchange_netflow("BTCUSDT")
    assert result.available
    assert result.metadata["provider"] == "bybit"


if __name__ == "__main__":
    test_score_coverage()
    test_public_collectors_with_mocks()
    test_engine_survives_source_failure()
    test_binance_failure_falls_back_to_bybit()
    print("smart money tests: OK")
