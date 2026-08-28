"""Runtime control plane for the v14 learning model.

V41 keeps operator-tunable model parameters in the persistent learning SQLite DB
instead of rewriting .env. Environment values remain the bootstrap/fallback.
All changes are audited and take effect on the next read/training cycle.
"""
from __future__ import annotations

import html
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable

from core.sqlite_utils import connect as sqlite_connect

DB_PATH = Path(os.getenv("LEARNING_V14_DB_PATH", "data/learning_v14.db"))

FEATURES = (
    "trend", "momentum", "volume", "funding", "open_interest", "alignment",
    "risk_reward", "capital_flow", "narrative", "news", "smart_money",
)

FEATURE_TITLES = {
    "trend": "Trend / тренд",
    "momentum": "Momentum / импульс",
    "volume": "Volume / объём",
    "funding": "Funding",
    "open_interest": "Open Interest",
    "alignment": "Timeframe Alignment",
    "risk_reward": "Risk / Reward",
    "capital_flow": "Capital Flow",
    "narrative": "Narrative",
    "news": "News Sentiment",
    "smart_money": "Smart Money",
}

FEATURE_DESCRIPTIONS = {
    "trend": "Сила и устойчивость направления цены. Больший вес сильнее продвигает монеты с чистым трендом и сильнее штрафует противотрендовые входы.",
    "momentum": "Скорость движения цены. Повышение веса делает модель чувствительнее к ускорению и пробоям, но на шумном рынке может увеличить число поздних входов.",
    "volume": "Подтверждение движения торговым объёмом. Больший вес сильнее требует реального участия рынка и обычно отсекает слабые пробои, но может пропускать ранние движения.",
    "funding": "Перекос perpetual-фьючерсов. Больший вес сильнее учитывает перегретость LONG/SHORT и стоимость удержания позиции; слишком большой вес может мешать на сильных трендах.",
    "open_interest": "Изменение открытого интереса. Повышение веса делает модель чувствительнее к приходу/выходу плечевого капитала и помогает отличать движение с набором позиций от пустого импульса.",
    "alignment": "Согласованность нескольких таймфреймов. Больший вес делает отбор более консервативным и предпочитает сделки, где младшие и старшие ТФ смотрят в одну сторону.",
    "risk_reward": "Качество потенциального соотношения прибыль/риск. Больший вес сильнее отдаёт приоритет сделкам с хорошим запасом до TP относительно SL.",
    "capital_flow": "Притоки/оттоки капитала и поток денег. Повышение веса усиливает влияние движения капитала и может раньше замечать накопление, но чувствительность зависит от качества источников.",
    "narrative": "Сила рыночного нарратива/темы. Больший вес помогает ловить секторные движения, но повышает зависимость от быстро меняющейся моды рынка.",
    "news": "Новостной sentiment. Повышение веса быстрее реагирует на новости, но увеличивает риск реакции на шум, неоднозначные заголовки и краткосрочные всплески.",
    "smart_money": "Признаки активности крупных участников. Больший вес сильнее учитывает whale/exchange/flow-сигналы; полезно при хорошем покрытии данных, опасно завышать при низком coverage.",
}


@dataclass(frozen=True)
class ParamSpec:
    key: str
    env: str
    title: str
    default: float
    minimum: float
    maximum: float
    step: float
    kind: str
    description: str
    raise_effect: str
    lower_effect: str
    caution: str


PARAMS: Dict[str, ParamSpec] = {
    "min_samples": ParamSpec(
        "min_samples", "LEARNING_MIN_SAMPLES", "Минимум samples", 200, 100, 2000, 25, "int",
        "Минимальное количество закрытых и пригодных для обучения наблюдений, прежде чем разрешается полноценное переобучение.",
        "Модель обучается реже, но на большей выборке. Обычно это повышает стабильность и уменьшает влияние случайной серии сделок.",
        "Модель начинает адаптироваться раньше. Это полезно после сильной смены рынка, но увеличивает риск переобучения на небольшой выборке.",
        "Слишком маленькое значение особенно опасно при большом числе признаков: модель может принять случайность за закономерность.",
    ),
    "specialist_min_samples": ParamSpec(
        "specialist_min_samples", "LEARNING_SPECIALIST_MIN_SAMPLES", "Samples для Specialist", 80, 50, 1000, 10, "int",
        "Минимальная выборка для отдельной модели конкретного режима рынка и направления LONG/SHORT.",
        "Specialist создаётся только когда по режиму накоплено много данных. Меньше ложной специализации, но новые режимы дольше используют глобальную модель.",
        "Специалисты появляются быстрее и лучше адаптируются к bull/bear/range, но могут быть нестабильными при малом числе примеров.",
        "Не стоит ставить сильно ниже общего min_samples: специалист должен иметь достаточно собственных наблюдений.",
    ),
    "walk_forward_folds": ParamSpec(
        "walk_forward_folds", "LEARNING_WALK_FORWARD_FOLDS", "Walk-Forward folds", 4, 2, 6, 1, "int",
        "Количество последовательных временных окон, на которых кандидат проверяется в walk-forward validation. Это защита от подгонки под один исторический участок.",
        "Проверка становится жёстче: модель должна работать устойчиво на большем количестве периодов. Обучение немного тяжелее, а улучшение труднее подтвердить.",
        "Обучение быстрее и кандидату легче пройти проверку, но возрастает риск, что результат хорош только на одном периоде рынка.",
        "Для небольшой выборки слишком много folds создаёт слишком короткие validation-окна.",
    ),
    "search_iterations": ParamSpec(
        "search_iterations", "LEARNING_SEARCH_ITERATIONS", "Search iterations", 240, 40, 800, 40, "int",
        "Сколько вариантов весов модель пробует при поиске более сильной комбинации признаков.",
        "Более глубокий поиск повышает шанс найти лучший набор весов, но увеличивает CPU-время и также расширяет пространство, в котором можно переоптимизироваться.",
        "Обучение быстрее и консервативнее, но модель может не найти улучшение, которое существует рядом с текущими весами.",
        "800 — верхняя граница движка. Большое число iterations не заменяет большую и качественную выборку.",
    ),
    "max_weight_change": ParamSpec(
        "max_weight_change", "LEARNING_MAX_WEIGHT_CHANGE", "Max изменение веса", 0.35, 0.05, 0.75, 0.05, "percent",
        "Максимальное отклонение каждого обучаемого веса от его базового значения за модельный поиск.",
        "Модель получает больше свободы и быстрее перестраивает приоритеты признаков. Это повышает адаптивность, но увеличивает риск резких и переобученных весов.",
        "Веса остаются ближе к исходной логике. Модель стабильнее, но может слишком медленно реагировать на структурные изменения рынка.",
        "Для постоянной 24/7 работы обычно лучше 15–35%. 50%+ стоит использовать только при большой выборке и строгом holdout.",
    ),
    "min_utility_gain": ParamSpec(
        "min_utility_gain", "LEARNING_MIN_UTILITY_GAIN", "Min Utility Gain", 0.012, 0.002, 0.05, 0.002, "percent",
        "Минимальное улучшение utility, которое challenger обязан показать относительно champion, чтобы иметь шанс стать активной моделью.",
        "Champion меняется реже и только при заметном улучшении. Система стабильнее, но может дольше держать слегка устаревшую модель.",
        "Новые модели продвигаются легче и быстрее. Адаптация ускоряется, но возрастает вероятность переключений из-за статистического шума.",
        "При высоком drift движок дополнительно повышает требуемый gain, поэтому этот параметр не является единственной защитой.",
    ),
    "recency_half_life_days": ParamSpec(
        "recency_half_life_days", "LEARNING_RECENCY_HALF_LIFE_DAYS", "Recency half-life", 30, 2, 180, 5, "int",
        "Через сколько дней вес старого наблюдения уменьшается примерно вдвое. Управляет тем, насколько модель доверяет свежим данным относительно старой истории.",
        "История забывается медленнее. Модель стабильнее и лучше использует редкие режимы, но медленнее адаптируется к изменившемуся рынку.",
        "Свежие сделки получают намного больший вес. Модель быстрее меняется вслед за рынком, но может переучиваться на краткосрочную фазу.",
        "Слишком короткий half-life фактически уменьшает полезный размер выборки даже при большом числе сохранённых samples.",
    ),
    "rule_min_samples": ParamSpec(
        "rule_min_samples", "LEARNING_RULE_MIN_SAMPLES", "Samples для Learning Rule", 14, 8, 200, 2, "int",
        "Минимальное число подтверждений для автоматического правила вида «при таких факторах увеличить/уменьшить score».",
        "Правил будет меньше, зато каждое лучше подтверждено статистикой. Обычно уменьшается шум в корректировках score.",
        "Модель быстрее создаёт новые правила и взаимодействия признаков, но часть правил может оказаться случайной.",
        "Очень низкое значение особенно опасно для interaction rules, где комбинации признаков встречаются реже.",
    ),
    "max_total_adjustment": ParamSpec(
        "max_total_adjustment", "LEARNING_MAX_TOTAL_ADJUSTMENT", "Max Rule Adjustment", 20, 2, 50, 2, "float",
        "Максимальная суммарная поправка к AI Score от всех сработавших выученных правил.",
        "Learning rules сильнее влияют на итоговый рейтинг и могут заметно переставлять кандидатов в TOP. Выше адаптивность, но выше риск чрезмерной коррекции.",
        "Базовые факторы доминируют, а learning rules работают как небольшая поправка. Поведение предсказуемее.",
        "Слишком большой cap позволяет нескольким коррелированным правилам фактически перебить основную модель.",
    ),
    "min_holdout_samples": ParamSpec(
        "min_holdout_samples", "LEARNING_MIN_HOLDOUT_SAMPLES", "Min Holdout Samples", 40, 30, 500, 10, "int",
        "Минимальный размер финального временного holdout, который оптимизатор не видел при подборе весов. Champion меняется только после этой проверки.",
        "Промоут challenger сложнее, но результат надёжнее. Особенно полезно при агрессивном поиске весов.",
        "Модель может обновляться раньше, однако итоговая независимая проверка становится менее статистически надёжной.",
        "Это один из главных safety-параметров. Для реальной статистики его не стоит уменьшать без необходимости.",
    ),
}

PROFILE_VALUES: Dict[str, Dict[str, float]] = {
    "safe": {
        "min_samples": 350, "specialist_min_samples": 140, "walk_forward_folds": 5,
        "search_iterations": 180, "max_weight_change": 0.18, "min_utility_gain": 0.020,
        "recency_half_life_days": 45, "rule_min_samples": 25, "max_total_adjustment": 12,
        "min_holdout_samples": 70,
    },
    "balanced": {key: spec.default for key, spec in PARAMS.items()},
    "aggressive": {
        "min_samples": 120, "specialist_min_samples": 50, "walk_forward_folds": 4,
        "search_iterations": 520, "max_weight_change": 0.45, "min_utility_gain": 0.008,
        "recency_half_life_days": 18, "rule_min_samples": 10, "max_total_adjustment": 28,
        "min_holdout_samples": 30,
    },
}

PROFILE_TITLES = {"safe": "🟢 Safe", "balanced": "🟡 Balanced", "aggressive": "🔴 Aggressive", "custom": "⚙️ Custom"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite_connect(DB_PATH, timeout=30)
    conn.row_factory = __import__("sqlite3").Row
    return conn


def initialize() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS model_runtime_settings(
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                updated_by TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS model_weight_controls(
                feature TEXT PRIMARY KEY,
                mode TEXT NOT NULL DEFAULT 'auto',
                base_weight REAL,
                bound_pct REAL NOT NULL DEFAULT 0.20,
                updated_at TEXT NOT NULL,
                updated_by TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS model_control_audit(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                key TEXT,
                old_value TEXT,
                new_value TEXT,
                updated_by TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


def _decode(value: str) -> Any:
    try:
        return json.loads(value)
    except Exception:
        return value


def _encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def runtime_value(param_key: str) -> float:
    spec = PARAMS[param_key]
    initialize()
    with _connect() as conn:
        row = conn.execute("SELECT value_json FROM model_runtime_settings WHERE key=?", (param_key,)).fetchone()
    if row:
        try:
            return float(_decode(row["value_json"]))
        except Exception:
            pass
    try:
        return float(os.getenv(spec.env, str(spec.default)))
    except Exception:
        return float(spec.default)


def calibration_valid() -> bool:
    initialize()
    with _connect() as conn:
        row=conn.execute("SELECT value_json FROM model_runtime_settings WHERE key='calibration_valid'").fetchone()
    if not row:
        return True
    return str(_decode(row["value_json"])).strip().lower() not in {"false","0","off","no"}


def mark_calibration_valid(valid: bool, updated_by: str = "training") -> None:
    initialize()
    with _connect() as conn:
        conn.execute("INSERT INTO model_runtime_settings(key,value_json,updated_at,updated_by) VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at,updated_by=excluded.updated_by", ("calibration_valid", _encode(bool(valid)), _now(), updated_by))


def runtime_env(env_name: str, fallback: Any) -> str:
    for key, spec in PARAMS.items():
        if spec.env == env_name:
            value = runtime_value(key)
            if spec.kind == "int":
                return str(int(round(value)))
            return str(value)
    return os.getenv(env_name, str(fallback))


def _coerce(spec: ParamSpec, value: float) -> float:
    value = max(spec.minimum, min(spec.maximum, float(value)))
    if spec.kind == "int":
        return float(int(round(value)))
    return round(value, 6)


def set_param(param_key: str, value: float, updated_by: str = "telegram") -> float:
    spec = PARAMS[param_key]
    value = _coerce(spec, value)
    old = runtime_value(param_key)
    initialize()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO model_runtime_settings(key,value_json,updated_at,updated_by) VALUES(?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at,updated_by=excluded.updated_by",
            (param_key, _encode(value), _now(), updated_by),
        )
        conn.execute(
            "INSERT INTO model_control_audit(action,key,old_value,new_value,updated_by,created_at) VALUES(?,?,?,?,?,?)",
            ("set_param", param_key, _encode(old), _encode(value), updated_by, _now()),
        )
    set_runtime_profile("custom", updated_by=updated_by, audit=False)
    return value


def adjust_param(param_key: str, direction: int, updated_by: str = "telegram") -> float:
    spec = PARAMS[param_key]
    return set_param(param_key, runtime_value(param_key) + spec.step * (1 if direction >= 0 else -1), updated_by)


def reset_param(param_key: str, updated_by: str = "telegram") -> float:
    spec = PARAMS[param_key]
    initialize()
    old = runtime_value(param_key)
    with _connect() as conn:
        conn.execute("DELETE FROM model_runtime_settings WHERE key=?", (param_key,))
        conn.execute(
            "INSERT INTO model_control_audit(action,key,old_value,new_value,updated_by,created_at) VALUES(?,?,?,?,?,?)",
            ("reset_param", param_key, _encode(old), "ENV/default", updated_by, _now()),
        )
    set_runtime_profile("custom", updated_by=updated_by, audit=False)
    return runtime_value(param_key)


def auto_learning_enabled() -> bool:
    initialize()
    with _connect() as conn:
        row = conn.execute("SELECT value_json FROM model_runtime_settings WHERE key='__auto_learning_enabled__'").fetchone()
    if not row:
        return str(os.getenv("SELF_LEARNING_ENABLED", "true")).lower() in {"1", "true", "yes", "on"}
    return bool(_decode(row["value_json"]))


def set_auto_learning(enabled: bool, updated_by: str = "telegram") -> bool:
    initialize()
    old = auto_learning_enabled()
    enabled = bool(enabled)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO model_runtime_settings(key,value_json,updated_at,updated_by) VALUES('__auto_learning_enabled__',?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at,updated_by=excluded.updated_by",
            (_encode(enabled), _now(), updated_by),
        )
        conn.execute(
            "INSERT INTO model_control_audit(action,key,old_value,new_value,updated_by,created_at) VALUES(?,?,?,?,?,?)",
            ("set_auto_learning", "__auto_learning_enabled__", _encode(old), _encode(enabled), updated_by, _now()),
        )
    return enabled


def current_profile() -> str:
    initialize()
    with _connect() as conn:
        row = conn.execute("SELECT value_json FROM model_runtime_settings WHERE key='__profile__'").fetchone()
    if not row:
        return "balanced"
    value = str(_decode(row["value_json"]) or "balanced")
    return value if value in PROFILE_TITLES else "custom"


def set_runtime_profile(profile: str, updated_by: str = "telegram", audit: bool = True) -> str:
    if profile not in PROFILE_TITLES:
        profile = "custom"
    initialize()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO model_runtime_settings(key,value_json,updated_at,updated_by) VALUES('__profile__',?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at,updated_by=excluded.updated_by",
            (_encode(profile), _now(), updated_by),
        )
        if audit:
            conn.execute(
                "INSERT INTO model_control_audit(action,key,old_value,new_value,updated_by,created_at) VALUES(?,?,?,?,?,?)",
                ("set_profile", "__profile__", None, profile, updated_by, _now()),
            )
    return profile


def apply_profile(profile: str, updated_by: str = "telegram") -> Dict[str, float]:
    if profile not in PROFILE_VALUES:
        raise ValueError("unknown profile")
    initialize()
    values = PROFILE_VALUES[profile]
    with _connect() as conn:
        for key, value in values.items():
            spec = PARAMS[key]
            value = _coerce(spec, value)
            conn.execute(
                "INSERT INTO model_runtime_settings(key,value_json,updated_at,updated_by) VALUES(?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at,updated_by=excluded.updated_by",
                (key, _encode(value), _now(), updated_by),
            )
        conn.execute(
            "INSERT INTO model_runtime_settings(key,value_json,updated_at,updated_by) VALUES('__profile__',?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at,updated_by=excluded.updated_by",
            (_encode(profile), _now(), updated_by),
        )
        conn.execute(
            "INSERT INTO model_control_audit(action,key,old_value,new_value,updated_by,created_at) VALUES(?,?,?,?,?,?)",
            ("apply_profile", "__profile__", None, profile, updated_by, _now()),
        )
    return {k: runtime_value(k) for k in PARAMS}


def all_params() -> Dict[str, float]:
    return {key: runtime_value(key) for key in PARAMS}


def _weight_row(feature: str) -> Dict[str, Any]:
    initialize()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM model_weight_controls WHERE feature=?", (feature,)).fetchone()
    if not row:
        return {"feature": feature, "mode": "auto", "base_weight": None, "bound_pct": 0.20}
    return dict(row)


def weight_control(feature: str, defaults: Dict[str, float] | None = None) -> Dict[str, Any]:
    if feature not in FEATURES:
        raise KeyError(feature)
    row = _weight_row(feature)
    base_default = float((defaults or {}).get(feature, 1.0))
    row["base_weight"] = float(row.get("base_weight") if row.get("base_weight") is not None else base_default)
    row["bound_pct"] = float(row.get("bound_pct") or 0.20)
    return row


def set_weight_control(feature: str, *, mode: str | None = None, base_weight: float | None = None,
                       bound_pct: float | None = None, updated_by: str = "telegram") -> Dict[str, Any]:
    if feature not in FEATURES:
        raise KeyError(feature)
    current = weight_control(feature)
    new_mode = str(mode or current["mode"]).lower()
    if new_mode not in {"auto", "manual", "bounded"}:
        raise ValueError("invalid weight mode")
    new_base = max(0.10, min(3.00, float(current["base_weight"] if base_weight is None else base_weight)))
    new_bound = max(0.05, min(0.75, float(current["bound_pct"] if bound_pct is None else bound_pct)))
    initialize()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO model_weight_controls(feature,mode,base_weight,bound_pct,updated_at,updated_by) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(feature) DO UPDATE SET mode=excluded.mode,base_weight=excluded.base_weight,bound_pct=excluded.bound_pct,updated_at=excluded.updated_at,updated_by=excluded.updated_by",
            (feature, new_mode, new_base, new_bound, _now(), updated_by),
        )
        conn.execute(
            "INSERT INTO model_control_audit(action,key,old_value,new_value,updated_by,created_at) VALUES(?,?,?,?,?,?)",
            ("set_weight", feature, _encode(current), _encode({"mode": new_mode, "base_weight": new_base, "bound_pct": new_bound}), updated_by, _now()),
        )
    with _connect() as conn:
        conn.execute("INSERT INTO model_runtime_settings(key,value_json,updated_at,updated_by) VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at,updated_by=excluded.updated_by", ("calibration_valid", "false", _now(), updated_by))
    return weight_control(feature)


def adjust_weight(feature: str, delta: float, updated_by: str = "telegram") -> Dict[str, Any]:
    current = weight_control(feature)
    return set_weight_control(feature, base_weight=float(current["base_weight"]) + delta, updated_by=updated_by)


def cycle_weight_mode(feature: str, updated_by: str = "telegram") -> Dict[str, Any]:
    current = weight_control(feature)
    order = ["auto", "bounded", "manual"]
    mode = order[(order.index(current["mode"]) + 1) % len(order)]
    return set_weight_control(feature, mode=mode, updated_by=updated_by)


def adjust_weight_bound(feature: str, delta: float, updated_by: str = "telegram") -> Dict[str, Any]:
    current = weight_control(feature)
    return set_weight_control(feature, bound_pct=float(current["bound_pct"]) + delta, updated_by=updated_by)


def reset_weight(feature: str, updated_by: str = "telegram") -> None:
    initialize()
    with _connect() as conn:
        old = conn.execute("SELECT * FROM model_weight_controls WHERE feature=?", (feature,)).fetchone()
        conn.execute("DELETE FROM model_weight_controls WHERE feature=?", (feature,))
        conn.execute(
            "INSERT INTO model_control_audit(action,key,old_value,new_value,updated_by,created_at) VALUES(?,?,?,?,?,?)",
            ("reset_weight", feature, _encode(dict(old)) if old else None, "auto/default", updated_by, _now()),
        )
        conn.execute("INSERT INTO model_runtime_settings(key,value_json,updated_at,updated_by) VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at,updated_by=excluded.updated_by", ("calibration_valid", "false", _now(), updated_by))


def apply_weight_policy(weights: Dict[str, float], defaults: Dict[str, float]) -> Dict[str, float]:
    """Apply operator policy to learned weights.

    auto:     use learned value unchanged;
    manual:   pin to operator base_weight;
    bounded:  keep learned value inside base_weight +/- bound_pct.
    """
    result = dict(weights)
    for feature in FEATURES:
        current = float(result.get(feature, defaults.get(feature, 1.0)))
        cfg = weight_control(feature, defaults)
        mode = cfg["mode"]
        base = float(cfg["base_weight"])
        bound = float(cfg["bound_pct"])
        if mode == "manual":
            current = base
        elif mode == "bounded":
            current = max(base * (1 - bound), min(base * (1 + bound), current))
        result[feature] = round(max(0.10, min(3.00, current)), 5)
    return result


def weight_controls(defaults: Dict[str, float] | None = None) -> Dict[str, Dict[str, Any]]:
    return {feature: weight_control(feature, defaults) for feature in FEATURES}


def activate_version(version: str, updated_by: str = "telegram") -> Dict[str, Any]:
    """Manually promote a stored local model version and its learning rules."""
    initialize()
    with _connect() as conn:
        target = conn.execute("SELECT * FROM model_versions WHERE version=?", (version,)).fetchone()
        if not target:
            return {"status": "not-found", "version": version}
        old = conn.execute("SELECT version FROM model_versions WHERE status='active' ORDER BY id DESC LIMIT 1").fetchone()
        old_version = old["version"] if old else None
        conn.execute("UPDATE model_versions SET status='retired' WHERE status='active'")
        conn.execute("UPDATE learning_rules SET active=0")
        conn.execute("UPDATE model_versions SET status='active', activated_at=? WHERE version=?", (_now(), version))
        conn.execute("UPDATE learning_rules SET active=1 WHERE model_version=?", (version,))
        conn.execute(
            "INSERT INTO model_control_audit(action,key,old_value,new_value,updated_by,created_at) VALUES(?,?,?,?,?,?)",
            ("activate_version", "model", old_version, version, updated_by, _now()),
        )
    cloud_sync = True
    try:
        from learning_checkpoint_manager import save_checkpoint
        save_checkpoint(DB_PATH, reason=f"manual-activate-{version}")
        from cloud_model_store import CloudModelStore
        cfg=json.loads(target["config_json"] or "{}")
        metrics=json.loads(target["metrics_json"] or "{}")
        store=CloudModelStore()
        cloud_sync=store.save_model({"version":version,"config":cfg,"metrics":metrics},"challenger",int(target["sample_count"] or 0))
        if cloud_sync:
            cloud_sync=store.promote_version_atomic("learning-v14", version)
    except Exception:
        cloud_sync=False
    try:
        mark_calibration_valid(True, updated_by=updated_by)
    except Exception:
        pass
    return {"status": "activated", "version": version, "previous": old_version, "cloud_sync": cloud_sync}


def recent_versions(limit: int = 6) -> list[Dict[str, Any]]:
    initialize()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT version,status,sample_count,created_at,activated_at,metrics_json FROM model_versions ORDER BY id DESC LIMIT ?",
            (max(1, min(12, int(limit))),),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["metrics"] = json.loads(item.pop("metrics_json") or "{}")
        except Exception:
            item["metrics"] = {}
        result.append(item)
    return result


def recent_audit(limit: int = 12) -> list[Dict[str, Any]]:
    initialize()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT action,key,old_value,new_value,updated_by,created_at FROM model_control_audit ORDER BY id DESC LIMIT ?",
            (max(1, min(50, int(limit))),),
        ).fetchall()
    return [dict(r) for r in rows]


def _fmt_param(key: str, value: float | None = None) -> str:
    spec = PARAMS[key]
    value = runtime_value(key) if value is None else value
    if spec.kind == "percent":
        return f"{value * 100:.1f}%"
    if spec.kind == "int":
        return str(int(round(value)))
    return f"{value:g}"


def profile_description(profile: str) -> str:
    if profile == "safe":
        return "Консервативный режим: больше данных перед обучением, меньшая свобода весов и более строгая проверка challenger. Меньше переключений модели и ниже риск переобучения."
    if profile == "aggressive":
        return "Быстрая адаптация: меньше данных до обучения, больше поисковых итераций и свободы весов. Модель быстрее реагирует на рынок, но заметно выше риск переоптимизации."
    if profile == "custom":
        return "Хотя бы один параметр менялся вручную. Модель использует твои текущие runtime-настройки, которые сохраняются после перезапуска."
    return "Сбалансированный режим для постоянной работы: умеренная скорость адаптации, walk-forward и holdout-защита без чрезмерной консервативности."


def build_control_home_text(diagnostics: Dict[str, Any]) -> str:
    active = diagnostics.get("active") or {}
    metrics = diagnostics.get("metrics") or {}
    drift = diagnostics.get("drift") or {}
    profile = current_profile()
    versions = recent_versions(4)
    challenger = next((v for v in versions if v.get("status") == "challenger"), None)
    try:
        from adaptive_model_manager import latest_models
        adaptive_versions = latest_models(4)
    except Exception:
        adaptive_versions = []
    adaptive_champion = next((v for v in adaptive_versions if v.get("status") == "champion"), None)
    adaptive_candidate = next((v for v in adaptive_versions if v.get("status") == "candidate"), None)
    lines = [
        "🧠 <b>НЕЙРОМОДЕЛЬ · CONTROL CENTER V41</b>", "",
        f"Профиль: <b>{PROFILE_TITLES.get(profile, profile)}</b>",
        f"Автообучение: <b>{'🟢 ВКЛ' if auto_learning_enabled() else '⚪ ВЫКЛ'}</b>",
        f"Champion: <code>{html.escape(str(active.get('version') or '—'))}</code>",
        f"Samples: <b>{diagnostics.get('samples', 0)}</b>",
        f"Drift: <b>{html.escape(str(drift.get('status') or 'n/a'))}</b> · {float(drift.get('score') or 0):.3f}",
    ]
    if metrics.get("samples"):
        lines += [
            f"Win Rate: <b>{float(metrics.get('overall_win_rate') or 0):.1f}%</b>",
            f"Top 25% return: <b>{float(metrics.get('top_avg_return') or 0):+.2f}%</b>",
            f"Brier: <b>{float(metrics.get('brier') or 0):.4f}</b>",
            f"Rank corr: <b>{float(metrics.get('rank_corr') or 0):+.3f}</b>",
        ]
    lines.append(f"Challenger v14: <code>{html.escape(str(challenger.get('version') if challenger else 'нет'))}</code>")
    lines.append(f"Adaptive champion: <code>{html.escape(str(adaptive_champion.get('version') if adaptive_champion else 'нет'))}</code>")
    lines.append(f"Adaptive candidate: <code>{html.escape(str(adaptive_candidate.get('version') if adaptive_candidate else 'нет'))}</code>")
    lines += ["", "<b>Что можно менять:</b>",
              "• параметры обучения и строгость promotion;",
              "• базовые веса каждого AI-фактора;",
              "• AUTO / BOUNDED / MANUAL режим веса;",
              "• готовый профиль Safe / Balanced / Aggressive;",
              "• вручную запустить обучение или откатить Champion.", "",
              "<i>Все изменения хранятся в persistent data/learning_v14.db и не требуют редактирования .env.</i>"]
    return "\n".join(lines)


def build_params_text() -> str:
    profile = current_profile()
    lines = ["⚙️ <b>ПАРАМЕТРЫ ОБУЧЕНИЯ</b>", "", f"Профиль: <b>{PROFILE_TITLES.get(profile, profile)}</b>",
             profile_description(profile), "", "<b>Текущие значения:</b>"]
    for key, spec in PARAMS.items():
        lines.append(f"• {spec.title}: <b>{_fmt_param(key)}</b>")
    lines += ["", "Нажми параметр, чтобы увидеть подробное объяснение: <b>что он меняет, что произойдёт при увеличении/уменьшении и где появляется риск переобучения.</b>"]
    return "\n".join(lines)


def build_param_text(param_key: str) -> str:
    spec = PARAMS[param_key]
    current = runtime_value(param_key)
    default = spec.default
    return "\n".join([
        f"⚙️ <b>{html.escape(spec.title)}</b>", "",
        f"Текущее: <b>{_fmt_param(param_key, current)}</b>",
        f"Balanced default: <b>{_fmt_param(param_key, default)}</b>",
        f"Допустимый диапазон: <b>{_fmt_param(param_key, spec.minimum)} – {_fmt_param(param_key, spec.maximum)}</b>", "",
        f"<b>Что контролирует</b>\n{html.escape(spec.description)}", "",
        f"⬆️ <b>Если увеличить</b>\n{html.escape(spec.raise_effect)}", "",
        f"⬇️ <b>Если уменьшить</b>\n{html.escape(spec.lower_effect)}", "",
        f"⚠️ <b>На что обратить внимание</b>\n{html.escape(spec.caution)}", "",
        "Изменение применяется как runtime-настройка сразу. Для параметров обучения эффект полностью проявится при следующем запуске обучения.",
    ])


def build_profiles_text() -> str:
    current = current_profile()
    return "\n".join([
        "🎛 <b>ПРОФИЛИ НЕЙРОМОДЕЛИ</b>", "",
        f"Сейчас: <b>{PROFILE_TITLES.get(current, current)}</b>", "",
        "🟢 <b>Safe</b>\n" + profile_description("safe"), "",
        "🟡 <b>Balanced</b>\n" + profile_description("balanced"), "",
        "🔴 <b>Aggressive</b>\n" + profile_description("aggressive"), "",
        "⚙️ <b>Custom</b> включается автоматически после ручной правки любого параметра.", "",
        "<i>Профиль меняет только настройки обучения. Он не открывает сделки сам и не меняет Paper risk management.</i>",
    ])


def build_weights_text(active_weights: Dict[str, float], defaults: Dict[str, float]) -> str:
    lines = ["⚖️ <b>ВЕСА AI-ФАКТОРОВ</b>", "",
             "Вес определяет, насколько сильно конкретный фактор влияет на AI Score относительно остальных. Нажми фактор для подробного объяснения и управления.", ""]
    controls = weight_controls(defaults)
    for feature in FEATURES:
        cfg = controls[feature]
        mode_icon = {"auto": "🤖", "bounded": "🛡", "manual": "✋"}.get(cfg["mode"], "•")
        lines.append(f"{mode_icon} {FEATURE_TITLES[feature]}: <b>{float(active_weights.get(feature, defaults.get(feature,1))):.2f}</b> · {cfg['mode'].upper()}")
    lines += ["", "<b>Режимы:</b>",
              "🤖 AUTO — полностью доверять обученной модели.",
              "🛡 BOUNDED — модель учится, но не выходит дальше заданного коридора от твоего base weight.",
              "✋ MANUAL — зафиксировать твой base weight; обучение этот фактор не меняет."]
    return "\n".join(lines)


def build_weight_text(feature: str, learned_weight: float, effective_weight: float, defaults: Dict[str, float]) -> str:
    cfg = weight_control(feature, defaults)
    mode = cfg["mode"]
    base = float(cfg["base_weight"])
    bound = float(cfg["bound_pct"])
    low, high = base * (1 - bound), base * (1 + bound)
    mode_desc = {
        "auto": "AUTO: используется вес, выбранный Champion. Base weight хранится, но не ограничивает модель.",
        "bounded": f"BOUNDED: модель может изменять вес только в коридоре {low:.2f}–{high:.2f} (±{bound*100:.0f}% от base).",
        "manual": "MANUAL: итоговый вес жёстко равен base weight. Обученная величина для этого фактора игнорируется при scoring.",
    }[mode]
    return "\n".join([
        f"⚖️ <b>{html.escape(FEATURE_TITLES[feature])}</b>", "",
        html.escape(FEATURE_DESCRIPTIONS[feature]), "",
        f"Champion learned: <b>{learned_weight:.3f}</b>",
        f"Base weight: <b>{base:.3f}</b>",
        f"Effective now: <b>{effective_weight:.3f}</b>",
        f"Mode: <b>{mode.upper()}</b>",
        f"Bound: <b>±{bound*100:.0f}%</b>", "",
        f"<b>Как работает режим</b>\n{html.escape(mode_desc)}", "",
        "⬆️ <b>Увеличение base weight</b> делает этот сигнал важнее в итоговом AI Score. Монеты с сильным значением этого фактора поднимаются в рейтинге сильнее.", "",
        "⬇️ <b>Уменьшение base weight</b> снижает влияние фактора и делает модель менее чувствительной к его ошибкам/шуму.", "",
        "⚠️ <b>Риск:</b> сильно завышенный вес одного коррелированного фактора может дважды учитывать одну и ту же рыночную информацию. Для обычной работы лучше AUTO или BOUNDED.",
    ])


def build_versions_text() -> str:
    versions = recent_versions(8)
    try:
        from adaptive_model_manager import latest_models
        adaptive_versions = latest_models(5)
    except Exception:
        adaptive_versions = []
    lines = ["🏆 <b>CHAMPION / CHALLENGER</b>", "",
             "V14 Champion формирует AI Score и калибровку. Adaptive Champion — независимый Paper-trained слой, который затем может корректировать вероятность Hedge-модели.", "",
             "<b>V14 learning:</b>"]
    if not versions:
        lines.append("Сохранённых локальных V14-версий пока нет.")
    for row in versions:
        status = str(row.get("status") or "")
        icon = "🏆" if status == "active" else ("🥈" if status == "challenger" else "▫️")
        metrics = row.get("metrics") or {}
        cand = metrics.get("candidate") or {}
        lines.append(f"{icon} <code>{html.escape(str(row.get('version')))}</code> · <b>{html.escape(status)}</b> · n={row.get('sample_count',0)}")
        if cand:
            lines.append(f"   utility {float(cand.get('utility') or 0):.4f} · Brier {float(cand.get('brier') or 0):.4f} · PF {float(cand.get('profit_factor') or 0):.2f}")

    lines += ["", "<b>Adaptive Paper model:</b>"]
    if not adaptive_versions:
        lines.append("Adaptive-версий пока нет или Supabase недоступен.")
    for row in adaptive_versions:
        status = str(row.get("status") or "")
        icon = "🏆" if status == "champion" else ("🥈" if status == "candidate" else "▫️")
        metrics = row.get("metrics") or {}
        lines.append(f"{icon} <code>{html.escape(str(row.get('version')))}</code> · <b>{html.escape(status)}</b> · train={row.get('samples_train',0)} · val={row.get('samples_validation',0)}")
        if metrics:
            lines.append(f"   logloss {float(metrics.get('log_loss') or 0):.4f} · Brier {float(metrics.get('brier') or 0):.4f} · accuracy {float(metrics.get('accuracy') or 0)*100:.1f}%")
    lines += ["", "Автообучение в Control Center теперь управляет <b>обоими</b> обучаемыми контурами. Ручной rollback ниже относится к V14 Champion; Adaptive слой продвигается только через собственный holdout safety-gate."]
    return "\n".join(lines)


def build_audit_text() -> str:
    rows = recent_audit(15)
    lines = ["📜 <b>ИСТОРИЯ ИЗМЕНЕНИЙ МОДЕЛИ</b>", ""]
    if not rows:
        return "\n".join(lines + ["Изменений пока нет."])
    for row in rows:
        ts = str(row.get("created_at") or "").replace("T", " ")[:19]
        lines.append(f"• {ts} · <b>{html.escape(str(row.get('action')))}</b> · {html.escape(str(row.get('key') or ''))}")
    return "\n".join(lines)
