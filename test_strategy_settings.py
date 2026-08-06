import os
import pytest
from strategy_settings import SPEC_BY_KEY, apply_value, current_value


def test_float_setting_applies_to_environment():
    old = os.environ.get("HEDGE_MIN_QUALITY")
    try:
        assert apply_value("HEDGE_MIN_QUALITY", "75,5") == "75.5"
        assert current_value("HEDGE_MIN_QUALITY") == "75.5"
    finally:
        if old is None:
            os.environ.pop("HEDGE_MIN_QUALITY", None)
        else:
            os.environ["HEDGE_MIN_QUALITY"] = old


def test_boolean_setting():
    old = os.environ.get("POSITION_SIZING_ENABLED")
    try:
        assert apply_value("POSITION_SIZING_ENABLED", "off") == "false"
        assert current_value("POSITION_SIZING_ENABLED") == "false"
    finally:
        if old is None:
            os.environ.pop("POSITION_SIZING_ENABLED", None)
        else:
            os.environ["POSITION_SIZING_ENABLED"] = old


def test_range_validation():
    with pytest.raises(ValueError):
        apply_value("TRADE_MIN_SCORE", 101)
