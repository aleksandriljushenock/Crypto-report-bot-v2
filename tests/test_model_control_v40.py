from pathlib import Path

import model_control as mc
import learning_engine_v14 as le
from telegram_ui import keyboards


def _temp_db(monkeypatch, tmp_path):
    db = tmp_path / "learning_v14.db"
    monkeypatch.setattr(mc, "DB_PATH", db)
    monkeypatch.setattr(le, "DB_PATH", db)
    return db


def test_runtime_param_persists_and_overrides_env(monkeypatch, tmp_path):
    _temp_db(monkeypatch, tmp_path)
    monkeypatch.setenv("LEARNING_SEARCH_ITERATIONS", "111")
    assert mc.runtime_value("search_iterations") == 111
    mc.set_param("search_iterations", 320, updated_by="test")
    assert mc.runtime_value("search_iterations") == 320
    assert le._runtime_env("LEARNING_SEARCH_ITERATIONS", "240") == "320"


def test_profiles_and_custom_state(monkeypatch, tmp_path):
    _temp_db(monkeypatch, tmp_path)
    mc.apply_profile("safe", updated_by="test")
    assert mc.current_profile() == "safe"
    assert mc.runtime_value("max_weight_change") == 0.18
    mc.adjust_param("max_weight_change", +1, updated_by="test")
    assert mc.current_profile() == "custom"


def test_weight_modes_apply_to_effective_weights(monkeypatch, tmp_path):
    _temp_db(monkeypatch, tmp_path)
    defaults = {feature: 1.0 for feature in mc.FEATURES}
    learned = dict(defaults)
    learned["trend"] = 1.8

    mc.set_weight_control("trend", mode="manual", base_weight=1.25, updated_by="test")
    assert mc.apply_weight_policy(learned, defaults)["trend"] == 1.25

    mc.set_weight_control("trend", mode="bounded", base_weight=1.0, bound_pct=0.20, updated_by="test")
    assert mc.apply_weight_policy(learned, defaults)["trend"] == 1.2

    mc.set_weight_control("trend", mode="auto", updated_by="test")
    assert mc.apply_weight_policy(learned, defaults)["trend"] == 1.8


def test_auto_learning_toggle_is_persistent(monkeypatch, tmp_path):
    _temp_db(monkeypatch, tmp_path)
    assert mc.set_auto_learning(False, updated_by="test") is False
    assert mc.auto_learning_enabled() is False
    assert mc.set_auto_learning(True, updated_by="test") is True
    assert mc.auto_learning_enabled() is True


def test_v40_callback_data_stays_under_telegram_limit(monkeypatch, tmp_path):
    _temp_db(monkeypatch, tmp_path)
    markups = [
        keyboards.model_control_keyboard(),
        keyboards.model_params_keyboard(),
        keyboards.model_profiles_keyboard(),
        keyboards.model_weights_keyboard(),
    ]
    for key in mc.PARAMS:
        markups.append(keyboards.model_param_keyboard(key))
    for feature in mc.FEATURES:
        markups.append(keyboards.model_weight_keyboard(feature))
    for markup in markups:
        for row in markup["inline_keyboard"]:
            for button in row:
                data = button.get("callback_data")
                if data:
                    assert len(data.encode("utf-8")) <= 64
