"""Runtime strategy settings stored in Supabase and mirrored to os.environ.

Render ENV remains the safe fallback/bootstrap source. Values stored in
public.strategy_settings override ENV after startup and can be changed from the
Telegram admin menu without a redeploy.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

from core.logging_setup import get_logger

_logger = get_logger("strategy_settings")
_LOCK = threading.RLock()


@dataclass(frozen=True)
class SettingSpec:
    key: str
    kind: str
    default: Any
    category: str
    title: str
    description: str
    minimum: Optional[float] = None
    maximum: Optional[float] = None


SPECS: tuple[SettingSpec, ...] = (
    SettingSpec("TRADE_MIN_SCORE", "float", 72, "filters", "Мин. Score", "Минимальный технический Score.", 0, 100),
    SettingSpec("TRADE_MIN_PROBABILITY", "float", 70, "filters", "Мин. Probability", "Минимальная вероятность до Hedge-фильтра.", 0, 100),
    SettingSpec("TRADE_MIN_RR", "float", 2.3, "filters", "Мин. R/R", "Минимальное соотношение риск/прибыль.", 0.1, 20),
    SettingSpec("QUALITY_MIN_RR", "float", 2.3, "filters", "Quality мин. R/R", "Минимальный R/R профиля качества.", 0.1, 20),
    SettingSpec("HEDGE_MIN_QUALITY", "float", 70, "filters", "Мин. Quality", "Минимальный итоговый Quality Score.", 0, 100),
    SettingSpec("HEDGE_MIN_EV_PCT", "float", 2.0, "filters", "Мин. EV, %", "Минимальное ожидаемое значение сделки в процентах.", -20, 100),
    SettingSpec("RULE_WEIGHT_PROBABILITY_TREND_LIQUIDITY", "float", 10, "rules", "Prob+Trend+Liquidity", "Бонус сильной вероятности, тренда и ликвидности.", -30, 30),
    SettingSpec("RULE_WEIGHT_DAILY_MICRO_ALIGNMENT", "float", 8, "rules", "Daily+Micro alignment", "Бонус согласованности 1D и 5m.", -30, 30),
    SettingSpec("RULE_WEIGHT_FLOW_ALIGNMENT_VOLUME", "float", 5, "rules", "Flow+Alignment+Volume", "Вес комбинации потока, alignment и объёма.", -30, 30),
    SettingSpec("RULE_WEIGHT_SMART_PULLBACK_PROBABILITY", "float", 3, "rules", "Smart Pullback+Prob", "Вес Smart Money pullback с вероятностью.", -30, 30),
    SettingSpec("RULE_WEIGHT_SMART_PULLBACK_VOLUME", "float", 2, "rules", "Smart Pullback+Volume", "Вес Smart Money pullback с объёмом.", -30, 30),
    SettingSpec("RULE_WEIGHT_PULLBACK", "float", 5, "rules", "Pullback", "Базовый бонус pullback.", -30, 30),
    SettingSpec("RULE_WEIGHT_BREAKOUT", "float", -5, "rules", "Breakout", "Базовый вес breakout.", -30, 30),
    SettingSpec("POSITION_SIZING_ENABLED", "bool", True, "position", "Динамическая ставка", "Включить рекомендованный размер позиции."),
    SettingSpec("POSITION_SIZE_BASE_USD", "float", 3, "position", "Базовая ставка, $", "Размер обычного прошедшего сигнала.", 0, 100000),
    SettingSpec("POSITION_SIZE_STRONG_USD", "float", 4, "position", "Сильная ставка, $", "Размер позиции при сильной комбинации.", 0, 100000),
    SettingSpec("POSITION_SIZE_MAX_USD", "float", 5, "position", "Макс. ставка, $", "Максимальная рекомендация размера позиции.", 0, 100000),
    SettingSpec("PROFILE_RECENCY_ENABLED", "bool", True, "recency", "Recency включён", "Использовать свежие группы профиля, когда они доступны."),
    SettingSpec("PROFILE_HALF_LIFE_DAYS", "float", 14, "recency", "Half-life, дней", "Период полураспада веса истории.", 1, 365),
    SettingSpec("PROFILE_RECENT_WINDOW_DAYS", "int", 21, "recency", "Окно, дней", "Размер свежего временного окна.", 1, 365),
    SettingSpec("PROFILE_MIN_RECENT_SAMPLES", "int", 30, "recency", "Мин. свежих примеров", "Минимальная выборка для recent-профиля.", 1, 100000),
    SettingSpec("PROFILE_RECENT_WEIGHT", "float", 2.0, "recency", "Вес свежей истории", "Вес recent-профиля относительно общей истории.", 0, 20),
    SettingSpec("TRADE_TOP_LIQUID_SYMBOLS", "int", 30, "runtime", "Монет в скане", "Количество наиболее ликвидных монет для анализа.", 1, 500),
    SettingSpec("HEDGE_CANDIDATE_POOL", "int", 8, "runtime", "Hedge-кандидатов", "Количество кандидатов для тяжёлого Hedge-этапа.", 1, 100),
)

SPEC_BY_KEY: Dict[str, SettingSpec] = {spec.key: spec for spec in SPECS}
CATEGORY_TITLES = {
    "filters": "🎯 Фильтры",
    "rules": "🧩 Веса правил",
    "position": "💵 Размер позиции",
    "recency": "🕒 Свежесть истории",
    "runtime": "⚙️ Сканирование",
}


def _client():
    from cloud_client import get_supabase_client
    return get_supabase_client()


def _parse(spec: SettingSpec, value: Any) -> Any:
    if spec.kind == "bool":
        if isinstance(value, bool):
            parsed = value
        else:
            raw = str(value).strip().lower()
            if raw in {"1", "true", "yes", "on", "да", "вкл", "включено"}:
                parsed = True
            elif raw in {"0", "false", "no", "off", "нет", "выкл", "выключено"}:
                parsed = False
            else:
                raise ValueError("Используй true/false, on/off или да/нет")
    elif spec.kind == "int":
        parsed = int(float(str(value).replace(",", ".")))
    elif spec.kind == "float":
        parsed = float(str(value).replace(",", "."))
    else:
        parsed = str(value)
    if isinstance(parsed, (int, float)) and not isinstance(parsed, bool):
        if spec.minimum is not None and parsed < spec.minimum:
            raise ValueError(f"Минимум: {spec.minimum:g}")
        if spec.maximum is not None and parsed > spec.maximum:
            raise ValueError(f"Максимум: {spec.maximum:g}")
    return parsed


def _serialize(spec: SettingSpec, value: Any) -> str:
    parsed = _parse(spec, value)
    if spec.kind == "bool":
        return "true" if parsed else "false"
    if spec.kind == "int":
        return str(int(parsed))
    if spec.kind == "float":
        return f"{float(parsed):g}"
    return str(parsed)


def apply_value(key: str, value: Any) -> str:
    spec = SPEC_BY_KEY[key]
    serialized = _serialize(spec, value)
    with _LOCK:
        os.environ[key] = serialized
    return serialized


def current_value(key: str) -> str:
    spec = SPEC_BY_KEY[key]
    return _serialize(spec, os.getenv(key, spec.default))


def seed_rows() -> list[dict[str, Any]]:
    rows = []
    for spec in SPECS:
        rows.append({
            "key": spec.key,
            "value": current_value(spec.key),
            "value_type": spec.kind,
            "category": spec.category,
            "title": spec.title,
            "description": spec.description,
            "min_value": spec.minimum,
            "max_value": spec.maximum,
            "is_editable": True,
        })
    return rows


def load_from_supabase(seed_missing: bool = True) -> Dict[str, str]:
    """Apply Supabase overrides to os.environ and seed missing rows."""
    applied: Dict[str, str] = {}
    try:
        response = _client().table("strategy_settings").select("key,value").execute()
        rows = response.data or []
        existing = set()
        for row in rows:
            key = str(row.get("key") or "")
            if key not in SPEC_BY_KEY:
                continue
            existing.add(key)
            try:
                applied[key] = apply_value(key, row.get("value"))
            except Exception as exc:
                _logger.warning("Invalid strategy setting %s=%r: %s", key, row.get("value"), exc)
        if seed_missing:
            missing = [row for row in seed_rows() if row["key"] not in existing]
            if missing:
                _client().table("strategy_settings").upsert(missing, on_conflict="key").execute()
        _logger.info("Strategy settings loaded from Supabase: %s", len(applied))
    except Exception as exc:
        _logger.warning("Strategy settings Supabase load skipped; ENV fallback is active: %s", exc)
    return applied


def save_setting(key: str, value: Any, updated_by: Optional[str] = None) -> str:
    if key not in SPEC_BY_KEY:
        raise KeyError(f"Unknown setting: {key}")
    spec = SPEC_BY_KEY[key]
    old_value = current_value(key)
    serialized = _serialize(spec, value)
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "key": key,
        "value": serialized,
        "value_type": spec.kind,
        "category": spec.category,
        "title": spec.title,
        "description": spec.description,
        "min_value": spec.minimum,
        "max_value": spec.maximum,
        "is_editable": True,
        "updated_at": now,
        "updated_by": updated_by,
    }
    _client().table("strategy_settings").upsert(row, on_conflict="key").execute()
    try:
        _client().table("strategy_settings_history").insert({
            "setting_key": key,
            "old_value": old_value,
            "new_value": serialized,
            "changed_by": updated_by,
            "changed_at": now,
        }).execute()
    except Exception as exc:
        _logger.warning("Strategy setting history write failed: %s", exc)
    apply_value(key, serialized)
    return serialized


def reset_setting(key: str, updated_by: Optional[str] = None) -> str:
    spec = SPEC_BY_KEY[key]
    return save_setting(key, spec.default, updated_by=updated_by)


def settings_by_category(category: str) -> Iterable[SettingSpec]:
    return (spec for spec in SPECS if spec.category == category)
