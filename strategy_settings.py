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
    SettingSpec("TRADE_MIN_PROBABILITY", "float", 60, "filters", "Мин. Probability", "Минимальная вероятность до Hedge-фильтра.", 0, 100),
    SettingSpec("TRADE_MIN_RR", "float", 2.0, "filters", "Мин. R/R", "Минимальное соотношение риск/прибыль.", 0.1, 20),
    SettingSpec("QUALITY_MIN_RR", "float", 2.0, "filters", "Quality мин. R/R", "Минимальный R/R профиля качества.", 0.1, 20),
    SettingSpec("HEDGE_MIN_QUALITY", "float", 62, "filters", "Мин. Quality", "Минимальный итоговый Quality Score.", 0, 100),
    SettingSpec("HEDGE_MIN_EV_PCT", "float", 0.5, "filters", "Мин. EV, %", "Минимальное ожидаемое значение сделки в процентах.", -20, 100),
    SettingSpec("RULE_WEIGHT_PROBABILITY_TREND_LIQUIDITY", "float", 3, "rules", "Prob+Trend+Liquidity", "Бонус сильной вероятности, тренда и ликвидности.", -30, 30),
    SettingSpec("RULE_WEIGHT_DAILY_MICRO_ALIGNMENT", "float", 2, "rules", "Daily+Micro alignment", "Бонус согласованности 1D и 5m.", -30, 30),
    SettingSpec("RULE_WEIGHT_FLOW_ALIGNMENT_VOLUME", "float", 1.5, "rules", "Flow+Alignment+Volume", "Вес комбинации потока, alignment и объёма.", -30, 30),
    SettingSpec("RULE_WEIGHT_SMART_PULLBACK_PROBABILITY", "float", 1, "rules", "Smart Pullback+Prob", "Вес Smart Money pullback с вероятностью.", -30, 30),
    SettingSpec("RULE_WEIGHT_SMART_PULLBACK_VOLUME", "float", 0.75, "rules", "Smart Pullback+Volume", "Вес Smart Money pullback с объёмом.", -30, 30),
    SettingSpec("RULE_WEIGHT_PULLBACK", "float", 1.5, "rules", "Pullback", "Базовый бонус pullback.", -30, 30),
    SettingSpec("RULE_WEIGHT_BREAKOUT", "float", -1.5, "rules", "Breakout", "Базовый вес breakout.", -30, 30),
    SettingSpec("POSITION_SIZING_ENABLED", "bool", True, "position", "Динамическая ставка", "Включить рекомендованный размер позиции."),
    SettingSpec("POSITION_SIZE_BASE_USD", "float", 3, "position", "Базовая ставка, $", "Размер обычного прошедшего сигнала.", 0, 100000),
    SettingSpec("POSITION_SIZE_STRONG_USD", "float", 4, "position", "Сильная ставка, $", "Размер позиции при сильной комбинации.", 0, 100000),
    SettingSpec("POSITION_SIZE_MAX_USD", "float", 5, "position", "Макс. ставка, $", "Максимальная рекомендация размера позиции.", 0, 100000),
    SettingSpec("PROFILE_RECENCY_ENABLED", "bool", True, "recency", "Recency включён", "Использовать свежие группы профиля, когда они доступны."),
    SettingSpec("PROFILE_HALF_LIFE_DAYS", "float", 7, "recency", "Half-life, дней", "Период полураспада веса истории.", 1, 365),
    SettingSpec("PROFILE_RECENT_WINDOW_DAYS", "int", 14, "recency", "Окно, дней", "Размер свежего временного окна.", 1, 365),
    SettingSpec("PROFILE_MIN_RECENT_SAMPLES", "int", 30, "recency", "Мин. свежих примеров", "Минимальная выборка для recent-профиля.", 1, 100000),
    SettingSpec("PROFILE_RECENT_WEIGHT", "float", 2.0, "recency", "Вес свежей истории", "Вес recent-профиля относительно общей истории.", 0, 20),
    SettingSpec("PAPER_TRADING_ENABLED", "bool", True, "paper", "Paper Trading", "Автоматически открывать виртуальные сделки по финальным сигналам."),
    SettingSpec("PAPER_INITIAL_BALANCE_USD", "float", 100, "paper", "Стартовый баланс, $", "Начальный баланс paper-счёта.", 1, 1000000),
    SettingSpec("PAPER_MAX_OPEN_POSITIONS", "int", 10, "paper", "Макс. открытых позиций", "Ограничение одновременных виртуальных позиций.", 1, 100),
    SettingSpec("PAPER_ONE_POSITION_PER_SYMBOL", "bool", True, "paper", "Одна позиция на монету", "Не открывать повторную позицию по уже открытому символу."),
    SettingSpec("PAPER_MAX_LEVERAGE", "int", 20, "paper", "Макс. плечо", "Верхний предел автоматического плеча.", 1, 125),
    SettingSpec("PAPER_LIQUIDATION_BUFFER_PCT", "float", 0.5, "paper", "Запас после стопа, %", "Минимальный расчётный запас между стопом и ликвидацией.", 0.05, 20),
    SettingSpec("PAPER_MAINTENANCE_MARGIN_PCT", "float", 0.5, "paper", "Maintenance margin, %", "Консервативная поправка для оценки ликвидации.", 0, 10),
    SettingSpec("PAPER_FEE_PCT_PER_SIDE", "float", 0.06, "paper", "Комиссия за сторону, %", "Комиссия paper-сделки отдельно на вход и выход.", 0, 2),
    SettingSpec("PAPER_SLIPPAGE_PCT", "float", 0.03, "paper", "Проскальзывание, %", "Неблагоприятное проскальзывание при закрытии.", 0, 5),
    SettingSpec("PAPER_MAX_HOLD_HOURS", "int", 72, "paper", "Макс. удержание, часов", "Закрыть позицию по рынку после этого срока.", 1, 720),
    SettingSpec("PAPER_MIN_FREE_BALANCE_USD", "float", 5, "paper", "Резерв баланса, $", "Минимальная свободная сумма, которую paper-счёт не использует.", 0, 100000),
    SettingSpec("PAPER_ENTRY_MAX_WAIT_HOURS", "int", 12, "paper", "Ожидание входа, часов", "Максимальное время ожидания реального касания entry/trigger.", 1, 168),
    SettingSpec("PAPER_MAX_ENTRY_DEVIATION_PCT", "float", 0.50, "paper", "Макс. отклонение breakout, %", "Не догонять breakout, если рынок уже ушёл слишком далеко от trigger.", 0, 20),
    SettingSpec("PAPER_ENTRY_SLIPPAGE_PCT", "float", 0.03, "paper", "Проскальзывание входа, %", "Консервативное проскальзывание при market breakout fill.", 0, 5),
    SettingSpec("MULTI_EXCHANGE_UNIVERSE_ENABLED", "bool", True, "runtime", "Multi-Exchange Universe", "Собирать рынок сразу с нескольких futures-бирж."),
    SettingSpec("MULTI_EXCHANGE_MIN_VENUES", "int", 2, "runtime", "Мин. бирж на монету", "Сколько бирж минимум должны поддерживать монету. 1 даёт максимальное покрытие.", 1, 10),
    SettingSpec("MULTI_EXCHANGE_MIN_QUOTE_VOLUME_USDT", "float", 15000000, "runtime", "Мин. объём на бирже, $", "Минимальный 24h quote volume для включения монеты в общий universe.", 0, 1000000000000),
    SettingSpec("MULTI_EXCHANGE_COVERAGE_BONUS", "float", 0.08, "runtime", "Бонус покрытия бирж", "Небольшой бонус к рангу монеты за присутствие на нескольких биржах.", 0, 1),
    SettingSpec("TRADE_TOP_LIQUID_SYMBOLS", "int", 150, "runtime", "Монет в скане", "Количество наиболее ликвидных монет для анализа.", 1, 500),
    SettingSpec("TRADE_SCAN_BATCH_SIZE", "int", 16, "runtime", "Размер batch скана", "Сколько монет держать в памяти одновременно. Меньше = ниже RAM.", 2, 20),
    SettingSpec("FAST_SCAN_POOL_SIZE", "int", 500, "runtime", "Fast Scan pool", "Сколько ликвидных рынков быстро ранжировать до Deep Scan.", 50, 2000),
    SettingSpec("TRADE_SCAN_MAX_WORKERS", "int", 4, "runtime", "Параллельность", "Число одновременных задач Deep Scan. Больше ускоряет, но повышает RAM и API load.", 1, 8),
    SettingSpec("NEAR_SIGNAL_WATCH_ENABLED", "bool", True, "runtime", "Near Watch", "Чаще пересканировать кандидатов, близких к одному финальному порогу."),
    SettingSpec("NEAR_SIGNAL_RESCAN_MINUTES", "int", 5, "runtime", "Near re-scan, мин", "Интервал повторной проверки ближайших кандидатов.", 1, 60),
    SettingSpec("NEAR_SIGNAL_RESCAN_LIMIT", "int", 40, "runtime", "Near лимит", "Максимум Near-кандидатов на один быстрый повторный проход.", 1, 100),
    SettingSpec("NEAR_SIGNAL_MIN_DISTANCE_PCT", "float", 85, "runtime", "Near близость, %", "Минимальная близость к единственному непройденному порогу.", 50, 100),
    SettingSpec("SHADOW_SIGNALS_ENABLED", "bool", True, "runtime", "Shadow Signals", "Сохранять близкие отклонённые идеи для проверки фильтров."),
    SettingSpec("HEDGE_CANDIDATE_POOL", "int", 40, "runtime", "Hedge-кандидатов", "Количество кандидатов для тяжёлого Hedge-этапа.", 1, 100),
    SettingSpec("STRATEGY_LAB_AUTO_ENABLED", "bool", True, "runtime", "Strategy Lab авто", "Автоматически запускать Strategy Lab по таймеру."),
    SettingSpec("STRATEGY_LAB_AUTO_INTERVAL_MINUTES", "int", 30, "runtime", "Strategy Lab интервал, мин", "Интервал между автоматическими Strategy Lab запусками. В round-robin каждый тик анализирует одну стратегию.", 5, 1440),
    SettingSpec("STRATEGY_LAB_AUTO_NOTIFY_READY", "bool", False, "runtime", "Strategy Lab уведомления", "Присылать короткое уведомление, когда автоанализ нашёл READY setup."),
    SettingSpec("STRATEGY_LAB_PARALLEL_WITH_MAIN", "bool", True, "runtime", "Strategy Lab параллельно", "Разрешить Strategy Lab работать одновременно с основным торговым сканером."),
    SettingSpec("STRATEGY_LAB_SYNC_WITH_MAIN", "bool", True, "runtime", "Strategy Lab вместе со сканом", "При каждом полном цикле основного монитора запускать следующую стратегию round-robin параллельно."),
    SettingSpec("STRATEGY_LAB_PARALLEL_MAX_SYMBOLS", "int", 120, "runtime", "Strategy Lab parallel лимит", "Максимум монет одной стратегии, когда одновременно работает основной Deep Scan.", 10, 300),
    SettingSpec("STRATEGY_LAB_PARALLEL_THROTTLE_MS", "int", 100, "runtime", "Strategy Lab throttle, мс", "Небольшая пауза между монетами параллельного Strategy Lab для снижения API load.", 0, 5000),
    SettingSpec("NEAR_SIGNAL_TTL_HOURS", "int", 12, "runtime", "Near TTL, часов", "Сколько времени держать near-кандидата до удаления.", 1, 168),
    SettingSpec("SHADOW_SIGNAL_TTL_HOURS", "int", 24, "runtime", "Shadow TTL, часов", "Горизонт наблюдения за отклонёнными идеями.", 1, 168),
    SettingSpec("CHRONOS_ENABLED", "bool", True, "optimizer", "Chronos", "Включить Chronos для финальных кандидатов. Состояние хранится в Supabase."),
    SettingSpec("CHRONOS_FINALISTS", "int", 3, "optimizer", "Chronos finalists", "Максимум финальных сигналов за цикл для Chronos.", 1, 5),
    SettingSpec("AI_OPTIMIZER_ENABLED", "bool", True, "optimizer", "AI Optimizer", "Ежедневно анализировать Paper Trading и формировать рекомендации."),
    SettingSpec("AI_OPTIMIZER_INTERVAL_MINUTES", "int", 1440, "optimizer", "Интервал Optimizer, мин", "Как часто выполнять автоматический анализ стратегии.", 60, 10080),
    SettingSpec("AI_OPTIMIZER_MIN_TRADES", "int", 150, "optimizer", "Мин. сделок Optimizer", "Минимум закрытых Paper-сделок для рекомендаций по фильтрам.", 10, 1000),
    SettingSpec("AI_OPTIMIZER_MIN_RETENTION", "float", 0.70, "optimizer", "Мин. сохранение сделок", "Минимальная доля сделок, которую должен сохранять предложенный более строгий фильтр.", 0.30, 1.0),
    SettingSpec("ADAPTIVE_MODEL_ENABLED", "bool", True, "optimizer", "Adaptive Model", "Использовать подтвержденную Paper-модель как небольшой дополнительный голос."),
    SettingSpec("ADAPTIVE_MODEL_MIN_TRADES", "int", 150, "optimizer", "Мин. сделок модели", "Минимальная выборка для обучения новой adaptive-модели.", 20, 5000),
    SettingSpec("ADAPTIVE_MODEL_MIN_VALIDATION", "int", 30, "optimizer", "Мин. validation", "Минимум сделок в хронологической проверочной выборке.", 8, 1000),
    SettingSpec("ADAPTIVE_MODEL_BLEND_WEIGHT", "float", 0.10, "optimizer", "Вес Adaptive Model", "Максимальная доля adaptive probability в итоговой вероятности.", 0.0, 0.45),
)

# Per-strategy Telegram notifications. Defaults are intentionally OFF: a strategy
# must be explicitly opted in from its Strategy Lab screen.
from strategies.catalog import STRATEGIES as _LAB_STRATEGIES

STRATEGY_NOTIFICATION_SPECS: tuple[SettingSpec, ...] = tuple(
    SettingSpec(
        f"STRATEGY_NOTIFY_{spec.key.upper()}",
        "bool",
        False,
        "runtime",
        f"{spec.title}: уведомления",
        "Отправлять READY/FILLED/CLOSED Telegram-уведомления только для этой стратегии.",
    )
    for spec in _LAB_STRATEGIES
)
SPECS = SPECS + STRATEGY_NOTIFICATION_SPECS

SPEC_BY_KEY: Dict[str, SettingSpec] = {spec.key: spec for spec in SPECS}
CATEGORY_TITLES = {
    "filters": "🎯 Фильтры",
    "rules": "🧩 Веса правил",
    "position": "💵 Размер позиции",
    "recency": "🕒 Свежесть истории",
    "runtime": "⚙️ Сканирование",
    "paper": "🧪 Paper Trading",
    "optimizer": "🧠 AI Optimizer",
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
