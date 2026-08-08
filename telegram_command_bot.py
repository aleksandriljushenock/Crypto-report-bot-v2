import os
import subprocess
import sys
import threading
import time
import re
import html
from datetime import datetime, timedelta, timezone
from listing_cache import get_cache_stats
from listing_database import get_database_stats
from pathlib import Path
from early_discovery_pipeline import (
    run_early_discovery,
)
from early_discovery_report import (
    build_early_discovery_report,
)
from listing_hunter_pipeline import (
    run_listing_hunter,
)
from listing_hunter_report import (
    build_listing_hunter_report,
)
from new_listings_report import (
    build_new_listings_report,
)
from listing_pipeline import (
    run_incremental_listing_scan,
)
import requests
from core.http_client import http
from core.logging_setup import get_logger
from core.settings import settings
from trade_engine import run_trade_scan
from trade_market_client import get_provider_health_snapshot, probe_provider_health, get_last_universe_summary
from background_services import AutomationSupervisor, build_automation_status
from trade_monitor import TradeMonitor
from trade_signal_report import (
    build_monitor_status,
    build_recent_signals_report,
    build_trade_scan_report,
)
from trade_outcome_tracker import get_trade_performance, persist_trade_signal
from paper_trading import (
    ensure_account as ensure_paper_account,
    format_open_message as format_paper_open_message,
    format_pending_message as format_paper_pending_message,
    format_missed_message as format_paper_missed_message,
    get_open_positions as get_paper_positions,
    get_recent_trades as get_paper_trades,
    open_from_signal as open_paper_from_signal,
    performance as get_paper_performance,
    reset_account as reset_paper_account,
)
from trade_statistics_report import build_performance_report, build_watchlist_report
from trade_watchlist import get_watchlist, upsert_watch_candidate
from cloud_learning_store import CloudLearningStore
from serialization_utils import to_json_safe
from professional_report import build_professional_report
from capital_flow_engine import build_capital_flow_report
from smart_money_engine import build_smart_money_report
from narrative_engine import build_narrative_report
from sentiment_engine import build_sentiment_report
from portfolio_manager import portfolio_report, set_position, remove_position
from news_engine import build_news_report
from self_learning_engine import build_learning_report, build_model_status_report
from operational_reports import (build_market_report, build_regime_report, build_confidence_report, build_features_report, build_health_report)
from ai_intelligence import build_top_ai_report, build_ai_history_report
from strategy_settings import (
    CATEGORY_TITLES,
    SPEC_BY_KEY,
    current_value as get_strategy_setting_value,
    load_from_supabase as load_strategy_settings,
    reset_setting as reset_strategy_setting,
    save_setting as save_strategy_setting,
    settings_by_category,
)
from ai_optimizer import run_optimizer, get_latest_recommendations, apply_recommendation, reject_recommendation
from adaptive_model_manager import train_candidate, latest_models
from trade_signal_store import (
    get_monitor_settings,
    get_recent_signals,
    initialize_signal_store,
    set_monitor_settings,
)
BOT_TOKEN = settings.telegram_bot_token
ALLOWED_CHAT_ID = settings.telegram_chat_id

_logger = get_logger("telegram_bot")

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOCK_FILE = BASE_DIR / "report_running.lock"

POLL_TIMEOUT = 30
RETRY_DELAY = 5

early_discovery_thread = None
early_discovery_lock = threading.Lock()

report_thread = None
report_lock = threading.Lock()

trade_scan_thread = None
trade_scan_lock = threading.Lock()
trade_monitor = None
automation_supervisor = None
runtime_health_monitor = None
cloud_store = CloudLearningStore()
strategy_edit_pending = {}


def log(message):
    _logger.info("%s", message)


def telegram_request(method, payload=None, timeout=40):
    if not BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN отсутствует в .env"
        )

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/{method}"
    )

    response = http.post(
        url,
        json=payload or {},
        timeout=(settings.http_connect_timeout, timeout),
        raise_for_status=False,
    )

    if not response.ok:
        try:
            error_data = response.json()
        except Exception:
            error_data = {
                "raw": response.text,
            }

        raise RuntimeError(
            f"Telegram API error "
            f"{response.status_code}: "
            f"{error_data}"
        )

    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(
            f"Telegram API returned ok=false: "
            f"{data}"
        )

    return data.get("result")

TELEGRAM_MESSAGE_LIMIT = 3900


def split_telegram_message(
    text,
    limit=TELEGRAM_MESSAGE_LIMIT,
):
    text = str(text or "").strip()

    if not text:
        return []

    if len(text) <= limit:
        return [text]

    parts = []
    current = []

    current_length = 0

    for line in text.splitlines():
        line_with_break = line + "\n"

        if len(line_with_break) > limit:
            if current:
                parts.append(
                    "".join(current).rstrip()
                )
                current = []
                current_length = 0

            for start in range(
                0,
                len(line_with_break),
                limit,
            ):
                chunk = line_with_break[
                    start:start + limit
                ].rstrip()

                if chunk:
                    parts.append(chunk)

            continue

        if (
            current_length
            + len(line_with_break)
            > limit
        ):
            parts.append(
                "".join(current).rstrip()
            )

            current = []
            current_length = 0

        current.append(line_with_break)
        current_length += len(
            line_with_break
        )

    if current:
        parts.append(
            "".join(current).rstrip()
        )

    return [
        part
        for part in parts
        if part
    ]


def send_message(
    chat_id,
    text,
    reply_markup=None,
    parse_mode="HTML",
):
    parts = split_telegram_message(
        text
    )

    if not parts:
        log(
            "Попытка отправить пустое сообщение"
        )
        return None

    responses = []

    for index, part in enumerate(parts):
        payload = {
            "chat_id": chat_id,
            "text": part,
            "disable_web_page_preview": True,
        }

        if parse_mode:
            payload["parse_mode"] = (
                parse_mode
            )

        if (
            reply_markup is not None
            and index == len(parts) - 1
        ):
            payload["reply_markup"] = (
                reply_markup
            )

        try:
            response = telegram_request(
                "sendMessage",
                payload,
                timeout=30,
            )

            responses.append(response)

        except Exception as exc:
            log(
                "Ошибка отправки Telegram: "
                f"part={index + 1}/"
                f"{len(parts)}, "
                f"length={len(part)}, "
                f"error={exc}"
            )

            # Если проблема в HTML,
            # пробуем отправить этот кусок
            # обычным текстом.
            fallback_payload = {
                "chat_id": chat_id,
                "text": re.sub(
                    r"<[^>]+>",
                    "",
                    part,
                ),
                "disable_web_page_preview": (
                    True
                ),
            }

            if (
                reply_markup is not None
                and index == len(parts) - 1
            ):
                fallback_payload[
                    "reply_markup"
                ] = reply_markup

            fallback_response = (
                telegram_request(
                    "sendMessage",
                    fallback_payload,
                    timeout=30,
                )
            )

            responses.append(
                fallback_response
            )

    if len(responses) == 1:
        return responses[0]

    return responses


def delete_webhook():
    try:
        telegram_request(
            "deleteWebhook",
            {
                "drop_pending_updates": False,
            },
            timeout=20,
        )
        log("Webhook удален. Long polling включен.")
    except Exception as exc:
        log(f"Не удалось удалить webhook: {exc}")


def set_webhook(webhook_url, secret_token=None, drop_pending_updates=False):
    payload = {
        "url": str(webhook_url).rstrip("/"),
        "allowed_updates": ["message", "callback_query"],
        "drop_pending_updates": bool(drop_pending_updates),
    }
    if secret_token:
        payload["secret_token"] = str(secret_token)

    result = telegram_request("setWebhook", payload, timeout=30)
    log(f"Telegram webhook установлен: {payload['url']}")
    return result


def get_webhook_info():
    return telegram_request("getWebhookInfo", {}, timeout=20)


def set_bot_commands():
    commands = [
        {
            "command": "report",
            "description": "Запустить полный анализ рынка",
        },
        {
            "command": "status",
            "description": "Проверить состояние анализа",
        },
        {
            "command": "help",
            "description": "Показать доступные команды",
        },

        {
            "command": "progress",
            "description": "Показать прогресс базы листингов",
        },

        {
            "command": "hunter",
            "description": (
                "Проверить новые анонсы листингов"
            ),
        },
        {"command": "trade", "description": "Найти торговые входы сейчас"},
        {"command": "monitor_on", "description": "Включить фоновый мониторинг"},
        {"command": "monitor_off", "description": "Остановить фоновый мониторинг"},
        {"command": "monitor_status", "description": "Статус фонового мониторинга"},
        {"command": "signals", "description": "Последние торговые сигналы"},
        {"command": "watchlist", "description": "AI Watchlist монет"},
        {"command": "performance", "description": "Эффективность сигналов"},
        {"command": "paper", "description": "Paper Trading и PnL"},
        {"command": "server", "description": "CPU, RAM, disk и VPS status"},
        {"command": "automation_status", "description": "Статус фоновых сервисов"},
        {"command": "pro", "description": "Полный Professional отчет v8"},
        {"command": "flows", "description": "Потоки капитала"},
        {"command": "smartmoney", "description": "Smart Money события"},
        {"command": "narratives", "description": "Актуальные нарративы"},
        {"command": "sentiment", "description": "Fear & Greed"},
        {"command": "news", "description": "Важные новости"},
        {"command": "portfolio", "description": "Портфель"},
        {"command": "learn", "description": "Самообучение и веса"},
        {"command": "topai", "description": "TOP AI монеты v13"},
        {"command": "aihistory", "description": "История AI Score монеты"},

    ]

    try:
        telegram_request(
            "setMyCommands",
            {"commands": commands},
            timeout=20,
        )
        log("Команды Telegram зарегистрированы.")
    except Exception as exc:
        log(f"Не удалось зарегистрировать команды: {exc}")


def is_authorized(chat_id):
    return str(chat_id) == str(ALLOWED_CHAT_ID)


def is_report_running():
    global report_thread

    with report_lock:
        thread_running = report_thread is not None and report_thread.is_alive()

    return thread_running or LOCK_FILE.exists()


def remove_stale_lock():
    if not LOCK_FILE.exists():
        return

    age_seconds = time.time() - LOCK_FILE.stat().st_mtime

    # Если lock старше 30 минут, считаем его остатком аварийного запуска.
    if age_seconds > 30 * 60:
        try:
            LOCK_FILE.unlink()
            log("Удален устаревший lock-файл.")
        except OSError as exc:
            log(f"Не удалось удалить устаревший lock: {exc}")


def run_report(chat_id):
    global report_thread

    try:
        LOCK_FILE.write_text(
            datetime.now().isoformat(),
            encoding="utf-8",
        )

        send_message(
            chat_id,
            "⏳ Анализ запущен.\n"
            "Собираю данные Binance, выполняю Rule Engine и AI-анализ.",
        )

        log(f"Запуск app.py по команде chat_id={chat_id}")

        result = subprocess.run(
            [sys.executable, str(BASE_DIR / "app.py")],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=30 * 60,
        )

        if result.stdout:
            log("app.py STDOUT:")
            for line in result.stdout.splitlines():
                log(f"  {line}")

        if result.stderr:
            log("app.py STDERR:")
            for line in result.stderr.splitlines():
                log(f"  {line}")

        if result.returncode == 0:
            # Сам отчет уже отправляет telegram_sender.py.
            send_message(
                chat_id,
                "✅ Анализ завершен. Торговый бриф отправлен выше.",
            )
            log("Ручной анализ успешно завершен.")
        else:
            send_message(
                chat_id,
                "❌ Анализ завершился ошибкой.\n"
                "Проверь последний файл в папке logs.",
            )
            log(f"app.py завершился с кодом {result.returncode}")

    except subprocess.TimeoutExpired:
        send_message(
            chat_id,
            "❌ Анализ остановлен: превышено максимальное время выполнения 30 минут.",
        )
        log("app.py остановлен по timeout.")

    except Exception as exc:
        log(f"Ошибка ручного запуска: {exc}")

        try:
            send_message(
                chat_id,
                f"❌ Ошибка запуска анализа:\n{str(exc)[:500]}",
            )
        except Exception as send_exc:
            log(f"Не удалось отправить сообщение об ошибке: {send_exc}")

    finally:
        try:
            if LOCK_FILE.exists():
                LOCK_FILE.unlink()
        except OSError as exc:
            log(f"Не удалось удалить lock-файл: {exc}")

        with report_lock:
            report_thread = None


def start_report(chat_id):
    global report_thread

    remove_stale_lock()

    with report_lock:
        if (
            report_thread is not None
            and report_thread.is_alive()
        ) or LOCK_FILE.exists():
            send_message(
                chat_id,
                "⚠️ Анализ уже выполняется. Дождись завершения текущего запуска.",
            )
            return

        report_thread = threading.Thread(
            target=run_report,
            args=(chat_id,),
            daemon=True,
        )
        report_thread.start()

def _back_row(target="menu_main"):
    return [{"text": "⬅️ Назад", "callback_data": target}]


def _home_row():
    return [{"text": "🏠 Главное меню", "callback_data": "menu_main"}]


def main_keyboard():
    """Final user-first menu: six clear destinations, no technical clutter."""
    return {
        "inline_keyboard": [
            [{"text": "🔍 Сканировать сейчас", "callback_data": "trade_scan"}],
            [
                {"text": "🎯 Сигналы", "callback_data": "menu_signals"},
                {"text": "📈 Результаты", "callback_data": "menu_performance"},
            ],
            [
                {"text": "🤖 AI Центр", "callback_data": "menu_ai"},
                {"text": "📊 Рынок", "callback_data": "menu_analytics"},
            ],
            [{"text": "⚙️ Настройки", "callback_data": "menu_system"}],
        ]
    }


def signals_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "🔥 Последние", "callback_data": "recent_signals"},
                {"text": "💎 Лучший сигнал", "callback_data": "best_signal"},
            ],
            [
                {"text": "🧠 Почему AI выбрал", "callback_data": "explain_signal"},
                {"text": "⭐ Watchlist", "callback_data": "trade_watchlist"},
            ],
            [
                {"text": "📈 Точность сигналов", "callback_data": "trade_performance"},
                {"text": "📰 Новости", "callback_data": "ai_news"},
            ],
            [{"text": "⏱ Статус скана", "callback_data": "scan_status"}],
            _home_row(),
        ]
    }


def market_keyboard():
    """Legacy market tools remain available from Analytics -> Advanced."""
    return {
        "inline_keyboard": [
            [{"text": "📊 Полный Market Report", "callback_data": "run_report"}],
            [
                {"text": "💰 Capital Flow", "callback_data": "capital_flows"},
                {"text": "😨 Fear & Greed", "callback_data": "sentiment"},
            ],
            [
                {"text": "🧠 Нарративы", "callback_data": "narratives"},
                {"text": "📰 Новости", "callback_data": "ai_news"},
            ],
            [{"text": "⬅️ Аналитика", "callback_data": "menu_analytics"}],
            _home_row(),
        ]
    }


def trade_keyboard():
    """Legacy trading controls. Main user flow lives in Signals and Portfolio."""
    return {
        "inline_keyboard": [
            [{"text": "⚡ Запустить скан", "callback_data": "trade_scan"}],
            [
                {"text": "▶️ Монитор ON", "callback_data": "monitor_on"},
                {"text": "⏸ Монитор OFF", "callback_data": "monitor_off"},
            ],
            [
                {"text": "📡 Статус монитора", "callback_data": "monitor_status"},
                {"text": "⏱ Статус скана", "callback_data": "scan_status"},
            ],
            [{"text": "🎯 К сигналам", "callback_data": "menu_signals"}],
            _home_row(),
        ]
    }


def discovery_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "🔭 Early Discovery", "callback_data": "early_discovery"},
                {"text": "🚨 Listing Hunter", "callback_data": "listing_hunter"},
            ],
            [{"text": "🆕 Обновить базу листингов", "callback_data": "scan_new_100"}],
            [{"text": "📈 Прогресс базы", "callback_data": "listing_progress"}],
            [{"text": "⬅️ Аналитика", "callback_data": "menu_analytics"}],
            _home_row(),
        ]
    }


def ai_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "🏆 Champion / Модель", "callback_data": "model_status"},
                {"text": "🧬 Learning", "callback_data": "self_learning"},
            ],
            [
                {"text": "🏆 TOP AI", "callback_data": "top_ai"},
                {"text": "📚 AI History", "callback_data": "ai_history"},
            ],
            [{"text": "🧠 Статус Chronos", "callback_data": "chronos_status"}],
            [
                {"text": "🟢 Chronos ON", "callback_data": "chronos_on"},
                {"text": "⚪ Chronos OFF", "callback_data": "chronos_off"},
            ],
            [{"text": "🧠 AI Optimizer", "callback_data": "ai_optimizer"}],
            [{"text": "🐋 Smart Money", "callback_data": "smart_money"}],
            _home_row(),
        ]
    }



def _fmt_metric(v, digits=2):
    try:
        return f"{float(v):.{digits}f}"
    except Exception:
        return "—"


def build_ai_optimizer_text():
    recs = get_latest_recommendations(8)
    models = latest_models(3)
    lines = ["🧠 <b>AI OPTIMIZER + ADAPTIVE MODELS</b>", ""]
    try:
        from paper_trading import get_recent_trades
        closed = len(get_recent_trades(1000))
    except Exception:
        closed = 0
    lines.append(f"Закрытых Paper-сделок: <b>{closed}</b>")
    lines.append(f"Optimizer: <b>{'готов к анализу' if closed >= int(float(os.getenv('AI_OPTIMIZER_MIN_TRADES','20'))) else 'накапливает данные'}</b>")
    lines.append(f"Adaptive model: <b>{'готов к обучению' if closed >= int(float(os.getenv('ADAPTIVE_MODEL_MIN_TRADES','40'))) else 'накапливает данные'}</b>")
    lines.append("")
    if models:
        champion = next((m for m in models if m.get('status') == 'champion'), None)
        if champion:
            met = champion.get('metrics') or {}
            lines += [
                f"🏆 Champion: <code>{html.escape(str(champion.get('version')))}</code>",
                f"Validation: <b>{champion.get('samples_validation') or 0}</b> · LogLoss: <b>{_fmt_metric(met.get('log_loss'),3)}</b>",
                "",
            ]
        else:
            lines += ["🏆 Adaptive Champion: <b>ещё не выбран</b>", ""]
    else:
        lines += ["🏆 Adaptive Champion: <b>ещё не обучался</b>", ""]
    lines.append(f"Рекомендаций на подтверждение: <b>{len(recs)}</b>")
    if recs:
        for i, rec in enumerate(recs[:5], 1):
            metrics = rec.get('metrics') or {}
            key = rec.get('setting_key') or rec.get('symbol') or rec.get('kind')
            proposed = rec.get('proposed_value')
            line = f"{i}. <b>{html.escape(str(key))}</b>"
            if proposed is not None:
                line += f" → <code>{html.escape(str(proposed))}</code>"
            lines.append(line)
            reason = str(rec.get('reason') or '')
            if reason:
                lines.append(f"   {html.escape(reason[:180])}")
            if metrics.get('estimated_pnl_delta') is not None:
                lines.append(f"   ΔPnL ≈ <b>{_fmt_metric(metrics.get('estimated_pnl_delta'))}$</b> · сохранено {metrics.get('retention_pct','—')}% сделок")
    else:
        lines.append("Новых рекомендаций пока нет. Это нормально: система не меняет стратегию без достаточной статистики.")
    lines += ["", "Автоприменение выключено: изменения стратегии подтверждаются вручную."]
    return "\n".join(lines)


def ai_optimizer_keyboard():
    rows = [
        [
            {"text": "🔎 Анализ сейчас", "callback_data": "optimizer_run"},
            {"text": "🧬 Обучить модель", "callback_data": "adaptive_train"},
        ]
    ]
    for rec in get_latest_recommendations(5):
        rid = str(rec.get('id') or '')
        if not rid:
            continue
        key = str(rec.get('setting_key') or rec.get('symbol') or 'рекомендация')
        if rec.get('kind') == 'setting':
            rows.append([
                {"text": f"✅ {key[:18]}", "callback_data": f"opt_apply:{rid}"},
                {"text": "✖️", "callback_data": f"opt_reject:{rid}"},
            ])
        else:
            rows.append([{"text": f"✖️ Скрыть {key[:20]}", "callback_data": f"opt_reject:{rid}"}])
    rows.append([{"text": "⬅️ AI Центр", "callback_data": "menu_ai"}])
    rows.append(_home_row())
    return {"inline_keyboard": rows}

def performance_keyboard():
    """Most-used trading results in one compact place."""
    return {
        "inline_keyboard": [
            [
                {"text": "📅 Сегодня", "callback_data": "perf_today"},
                {"text": "📆 7 дней", "callback_data": "perf_week"},
            ],
            [
                {"text": "🏆 Монеты", "callback_data": "perf_coins"},
                {"text": "🎚 Фильтры", "callback_data": "perf_filters"},
            ],
            [
                {"text": "📂 Открытые", "callback_data": "paper_positions"},
                {"text": "📜 Сделки", "callback_data": "paper_history"},
            ],
            [
                {"text": "🏁 Путь к 50", "callback_data": "paper_goal"},
                {"text": "🧪 Paper", "callback_data": "paper_menu"},
            ],
            _home_row(),
        ]
    }


def portfolio_keyboard():
    """Backward-compatible alias for old callbacks."""
    return performance_keyboard()


def analytics_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "🔎 Сканер", "callback_data": "scanner_intelligence"},
                {"text": "🌍 Universe", "callback_data": "universe_dashboard"},
            ],
            [
                {"text": "🌡 Market Mood", "callback_data": "market_mood"},
                {"text": "🗺 Heat Map", "callback_data": "heat_map"},
            ],
            [
                {"text": "💎 Лучший сигнал", "callback_data": "best_signal"},
                {"text": "🏦 Биржи", "callback_data": "exchange_status"},
            ],
            [
                {"text": "🧩 Комбинации", "callback_data": "best_combos"},
                {"text": "🌍 Рынок + новости", "callback_data": "menu_market"},
            ],
            [{"text": "🔭 Discovery", "callback_data": "menu_discovery"}],
            _home_row(),
        ]
    }


def system_keyboard():
    # Daily operations live in one place: the live dashboard. Monitor controls
    # remain there, so the top-level Settings menu no longer duplicates them.
    return {
        "inline_keyboard": [
            [{"text": "🎛 Настройки стратегии", "callback_data": "strategy_settings"}],
            [
                {"text": "📟 Состояние", "callback_data": "dashboard"},
                {"text": "❤️ Health", "callback_data": "health_check"},
            ],
            [
                {"text": "🧩 Сервисы", "callback_data": "automation_status"},
                {"text": "🖥 Сервер", "callback_data": "server_status"},
            ],
            [{"text": "🛠 Диагностика", "callback_data": "bot_status"}],
            _home_row(),
        ]
    }

def strategy_settings_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "🎯 Фильтры", "callback_data": "cfg_cat:filters"},
                {"text": "🧩 Веса правил", "callback_data": "cfg_cat:rules"},
            ],
            [
                {"text": "💵 Размер позиции", "callback_data": "cfg_cat:position"},
                {"text": "🕒 Свежесть", "callback_data": "cfg_cat:recency"},
            ],
            [{"text": "⚙️ Сканирование", "callback_data": "cfg_cat:runtime"}],
            [{"text": "🧪 Paper Trading", "callback_data": "cfg_cat:paper"}],
            [{"text": "🧠 AI Optimizer", "callback_data": "cfg_cat:optimizer"}],
            [{"text": "🔄 Загрузить из Supabase", "callback_data": "cfg_reload"}],
            _back_row("menu_system"),
        ]
    }


def build_strategy_settings_text():
    lines = [
        "🎛 <b>НАСТРОЙКИ СТРАТЕГИИ</b>",
        "",
        "Значения хранятся в Supabase и применяются сразу, без деплоя.",
        "Render ENV используется как резерв при недоступности базы.",
        "",
        "Выбери раздел:",
    ]
    return "\n".join(lines)


def strategy_category_keyboard(category):
    rows = []
    for spec in settings_by_category(category):
        value = get_strategy_setting_value(spec.key)
        rows.append([{
            "text": f"{spec.title}: {value}",
            "callback_data": f"cfg_edit:{spec.key}",
        }])
    rows.append([{"text": "⬅️ К разделам", "callback_data": "strategy_settings"}])
    rows.append(_home_row())
    return {"inline_keyboard": rows}


def build_strategy_category_text(category):
    title = CATEGORY_TITLES.get(category, category)
    lines = [f"{title}", ""]
    for spec in settings_by_category(category):
        value = html.escape(get_strategy_setting_value(spec.key))
        lines.append(f"• <b>{html.escape(spec.title)}</b>: <code>{value}</code>")
    lines.extend(["", "Нажми параметр, чтобы изменить его."])
    return "\n".join(lines)


def build_strategy_edit_text(key):
    spec = SPEC_BY_KEY[key]
    value = html.escape(get_strategy_setting_value(key))
    bounds = []
    if spec.minimum is not None:
        bounds.append(f"min {spec.minimum:g}")
    if spec.maximum is not None:
        bounds.append(f"max {spec.maximum:g}")
    bounds_text = f" ({', '.join(bounds)})" if bounds else ""
    return (
        f"✏️ <b>{html.escape(spec.title)}</b>\n\n"
        f"Текущее значение: <code>{value}</code>\n"
        f"Тип: <code>{spec.kind}</code>{bounds_text}\n\n"
        f"{html.escape(spec.description)}\n\n"
        "Отправь новое значение обычным сообщением.\n"
        "Для отмены отправь <code>/cancel</code>."
    )


def strategy_edit_keyboard(key):
    spec = SPEC_BY_KEY[key]
    return {
        "inline_keyboard": [
            [{"text": "↩️ Значение по умолчанию", "callback_data": f"cfg_reset:{key}"}],
            [{"text": "⬅️ Назад", "callback_data": f"cfg_cat:{spec.category}"}],
            _home_row(),
        ]
    }

def _chronos_state_text():
    try:
        from chronos_forecaster import chronos_enabled
        enabled = chronos_enabled()
    except Exception:
        enabled = str(os.getenv("CHRONOS_ENABLED", "false")).strip().lower() in ("1", "true", "yes", "on")
    mode = str(os.getenv("CHRONOS_MODE", "subprocess") or "subprocess").strip().lower()
    return f"{'🟢' if enabled else '⚪'} {'включён' if enabled else 'выключен'} ({mode})"


def _runtime_memory_mb():
    """Return current RSS and process peak RSS without adding a psutil dependency."""
    current = None
    peak = None
    try:
        status = Path("/proc/self/status").read_text(encoding="utf-8", errors="ignore")
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                current = float(line.split()[1]) / 1024.0
            elif line.startswith("VmHWM:"):
                peak = float(line.split()[1]) / 1024.0
    except Exception:
        pass
    if peak is None:
        try:
            import resource
            value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            # Linux reports KiB; macOS reports bytes. Railway/Linux is the target.
            peak = value / 1024.0
        except Exception:
            pass
    return current, peak


def _format_elapsed(started_at):
    if not started_at:
        return None
    try:
        start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        now = datetime.now(start.tzinfo) if start.tzinfo else datetime.now()
        seconds = max(0, int((now - start).total_seconds()))
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}ч {minutes:02d}м {seconds:02d}с"
        return f"{minutes:02d}м {seconds:02d}с"
    except Exception:
        return None


def _scan_owner_label(owner):
    value = str(owner or "").strip().lower()
    mapping = {
        "automatic_monitor": "фоновый монитор",
        "monitor": "фоновый монитор",
        "manual_trade_scan": "ручной скан",
        "manual": "ручной скан",
        "near_signal_watch": "Near-Signal re-scan",
        "near_watch": "Near-Signal re-scan",
        "shadow": "Shadow update",
    }
    return mapping.get(value, value.replace("_", " ") if value else "неизвестно")


def _scan_phase_label(phase):
    value = str(phase or "idle").strip().lower()
    mapping = {
        "idle": "ожидание",
        "universe": "🌍 сбор Universe",
        "market_data": "⚡ Fast/market data",
        "analysis": "🔬 Deep Scan",
        "ranking": "🧠 ranking / AI",
        "hedge": "🧠 Hedge / AI",
        "finalizing": "✅ финализация результатов",
        "chronos": "🧠 Chronos",
        "near_signal": "🟡 Near Signals",
        "shadow": "👻 Shadow update",
    }
    return mapping.get(value, value.replace("_", " "))


def build_dashboard_text():
    monitor_settings = get_monitor_settings()
    monitor_enabled = bool(monitor_settings.get("enabled"))
    monitor_alive = bool(trade_monitor and trade_monitor.is_alive())
    manual_thread_alive = bool(trade_scan_thread and trade_scan_thread.is_alive())
    report_alive = bool(is_report_running())
    listing_alive = bool(new_scan_thread and new_scan_thread.is_alive())

    try:
        from trade_engine import is_trade_scan_running, get_trade_scan_runtime_state
        scan_state = get_trade_scan_runtime_state() or {}
        engine_busy = bool(is_trade_scan_running() or scan_state.get("running"))
    except Exception:
        scan_state = {}
        engine_busy = manual_thread_alive

    rss_mb, peak_mb = _runtime_memory_mb()
    fast_pool = int(os.getenv("FAST_SCAN_POOL_SIZE", "250"))
    deep_limit = int(os.getenv("TRADE_TOP_LIQUID_SYMBOLS", "80"))
    batch_size = int(os.getenv("TRADE_SCAN_BATCH_SIZE", "8"))
    workers = int(os.getenv("TRADE_SCAN_MAX_WORKERS", "2"))
    hedge_pool = int(os.getenv("HEDGE_CANDIDATE_POOL", "28"))

    lines = [
        "📟 <b>ПАНЕЛЬ СОСТОЯНИЯ</b>",
        "",
        f"📡 Монитор: <b>{'🟢 работает' if monitor_enabled and monitor_alive else ('🟡 включён, процесс не активен' if monitor_enabled else '⚪ остановлен')}</b>",
    ]

    if engine_busy:
        owner = _scan_owner_label(scan_state.get("owner"))
        phase = _scan_phase_label(scan_state.get("phase"))
        processed = int(scan_state.get("processed") or 0)
        total = int(scan_state.get("total") or 0)
        elapsed = _format_elapsed(scan_state.get("startedAt"))
        lines.extend([
            "",
            "🔍 Сканер: <b>🟡 выполняется</b>",
            f"├ Источник: <b>{html.escape(owner)}</b>",
            f"├ Этап: <b>{html.escape(phase)}</b>",
        ])
        if total > 0:
            pct = min(100, max(0, int(processed * 100 / total)))
            lines.append(f"├ Прогресс: <b>{processed}/{total}</b> ({pct}%)")
        else:
            lines.append("├ Прогресс: <b>подготовка...</b>")
        if elapsed:
            lines.append(f"└ В работе: <b>{elapsed}</b>")
    else:
        lines.extend([
            "",
            "🔍 Сканер: <b>🟢 готов</b>",
            "└ Активного ручного или фонового прохода нет",
        ])
        try:
            from scanner_intelligence import get_last_scan_intelligence
            last = get_last_scan_intelligence() or {}
            stages = last.get("stages") or {}
            last_at = last.get("runTimeUtc") or last.get("savedAt")
            if last_at:
                dt = datetime.fromisoformat(str(last_at).replace("Z", "+00:00"))
                lines.append(f"   Последний проход: <b>{dt.astimezone().strftime('%H:%M:%S')}</b>")
            if stages:
                lines.append(
                    f"   Проверено: <b>{int(stages.get('analyzed') or 0)}</b> · "
                    f"сигналов: <b>{int(stages.get('signals') or 0)}</b>"
                )
        except Exception:
            pass

    try:
        from core.runtime_state import get as runtime_component
        heavy_state = runtime_component("heavy_task")
    except Exception:
        heavy_state = {}

    lines.extend([
        "",
        f"⚡ Ручной запуск: <b>{'занят общим сканером' if engine_busy else ('выполняется' if manual_thread_alive else 'доступен')}</b>",
        f"🧠 Chronos: <b>{_chronos_state_text()}</b>",
    ])
    if heavy_state.get("running"):
        heavy_name = str(heavy_state.get("name") or "background task").replace("-", " ")
        lines.append(f"⚙️ Фоновая задача: <b>🟡 {html.escape(heavy_name)}</b>")
    else:
        lines.append("⚙️ Фоновая задача: <b>🟢 нет тяжёлых задач</b>")

    extra_tasks = []
    if report_alive:
        extra_tasks.append("market report")
    if listing_alive:
        extra_tasks.append("база листингов")
    if extra_tasks:
        lines.append(f"⚙️ Другие задачи: <b>{html.escape(', '.join(extra_tasks))}</b>")

    lines.extend([
        "",
        "🔎 <b>Параметры сканера</b>",
        f"Fast pool: <b>{fast_pool}</b> · Deep: <b>{deep_limit}</b>",
        f"Batch: <b>{batch_size}</b> · Workers: <b>{workers}</b> · Hedge: <b>{hedge_pool}</b>",
    ])
    if rss_mb is not None or peak_mb is not None:
        current_text = f"{rss_mb:.0f} MB" if rss_mb is not None else "N/A"
        peak_text = f"{peak_mb:.0f} MB" if peak_mb is not None else "N/A"
        lines.extend([
            "",
            "🧠 <b>Процесс бота</b>",
            f"RAM: <b>{current_text}</b> · Peak: <b>{peak_text}</b>",
        ])

    lines.extend([
        "",
        f"🕒 Обновлено: <b>{datetime.now().strftime('%H:%M:%S')}</b>",
        "",
        "Нажми «🔄 Обновить панель» для актуального состояния.",
    ])
    return "\n".join(lines)


def dashboard_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "⚡ Запустить скан", "callback_data": "trade_scan"}],
            [
                {"text": "▶️ Монитор ON", "callback_data": "monitor_on"},
                {"text": "⏸ Монитор OFF", "callback_data": "monitor_off"},
            ],
            [
                {"text": "🔥 Сигналы", "callback_data": "recent_signals"},
                {"text": "📈 Результаты", "callback_data": "trade_performance"},
            ],
            [
                {"text": "🟢 Chronos ON", "callback_data": "chronos_on"},
                {"text": "⚪ Chronos OFF", "callback_data": "chronos_off"},
            ],
            [{"text": "🔄 Обновить панель", "callback_data": "dashboard"}],
            _home_row(),
        ]
    }


def _signal_payload(row):
    payload = row.get("payload") if isinstance(row, dict) else None
    return payload if isinstance(payload, dict) else {}


def _num(value, default=0.0):
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return float(default)


def _signal_quality(row):
    p = _signal_payload(row)
    return _num(p.get("qualityScore", p.get("quality_score", row.get("quality_score") if isinstance(row, dict) else 0)))


def _signal_probability(row):
    p = _signal_payload(row)
    return _num(p.get("calibratedProbability", p.get("probability", row.get("probability") if isinstance(row, dict) else 0)))


def _signal_ev(row):
    p = _signal_payload(row)
    return _num(p.get("expectedValuePct", p.get("expected_value_pct", 0)))


def _signal_direction(row):
    p = _signal_payload(row)
    return str(p.get("direction") or (row.get("direction") if isinstance(row, dict) else "") or "").upper()


def _signal_symbol(row):
    p = _signal_payload(row)
    return str(p.get("symbol") or (row.get("symbol") if isinstance(row, dict) else "") or "?").upper()


def _paper_snapshot():
    try:
        stats = get_paper_performance() or {}
        account = stats.get("account") or {}
        return {
            "balance": _num(account.get("balance")),
            "initial": _num(account.get("initial_balance"), 100),
            "pnl": _num(stats.get("net_pnl"), account.get("realized_pnl") or 0),
            "closed": int(stats.get("closed_count") or 0),
            "open": len(stats.get("open_positions") or []),
            "win_rate": _num(stats.get("win_rate")),
            "pf": _num(stats.get("profit_factor")),
        }
    except Exception as exc:
        log(f"Paper snapshot error: {exc}")
        return {"balance": 0, "initial": 100, "pnl": 0, "closed": 0, "open": 0, "win_rate": 0, "pf": 0}


def build_best_signal_text():
    rows = get_recent_signals(limit=50)
    if not rows:
        return "💎 <b>ЛУЧШИЙ СИГНАЛ</b>\n\nСигналов пока нет."
    best = max(rows, key=lambda r: (_signal_quality(r), _signal_ev(r), _signal_probability(r)))
    p = _signal_payload(best)
    symbol = html.escape(_signal_symbol(best))
    direction = "SHORT" if "SHORT" in _signal_direction(best) else "LONG"
    score = _num(p.get("score", best.get("score", 0)))
    quality = _signal_quality(best)
    prob = _signal_probability(best)
    ev = _signal_ev(best)
    rr = _num(p.get("rr", best.get("rr", 0)))
    return (
        "💎 <b>ЛУЧШИЙ СИГНАЛ ИЗ ПОСЛЕДНИХ 50</b>\n\n"
        f"<b>{symbol} {direction}</b>\n"
        f"Score: <b>{score:.0f}</b>\n"
        f"Quality: <b>{quality:.1f}</b>\n"
        f"Probability: <b>{prob:.1f}%</b>\n"
        f"EV: <b>{ev:+.2f}%</b>\n"
        f"R/R: <b>{rr:.2f}</b>"
    )


def build_explain_signal_text():
    rows = get_recent_signals(limit=1)
    if not rows:
        return "🧠 <b>ПОЧЕМУ AI ВЫБРАЛ СИГНАЛ</b>\n\nСигналов пока нет."
    row = rows[0]
    p = _signal_payload(row)
    positive = p.get("positiveProfileHits") or p.get("positive_profile_hits") or []
    anti = p.get("antiProfileHits") or p.get("anti_profile_hits") or []
    reasons = p.get("aiReasons") or p.get("qualityRules") or p.get("quality_rules") or []
    if isinstance(positive, str): positive = [positive]
    if isinstance(anti, str): anti = [anti]
    if isinstance(reasons, str): reasons = [reasons]
    lines = [
        "🧠 <b>ПОЧЕМУ AI ВЫБРАЛ ПОСЛЕДНИЙ СИГНАЛ</b>", "",
        f"<b>{html.escape(_signal_symbol(row))}</b> • Quality <b>{_signal_quality(row):.1f}</b> • EV <b>{_signal_ev(row):+.2f}%</b>", ""
    ]
    if positive:
        lines.append("✅ <b>Сильные профили</b>")
        lines.extend(f"• {html.escape(str(x))}" for x in positive[:5])
    if reasons:
        lines.append("\n📌 <b>Ключевые причины</b>")
        lines.extend(f"• {html.escape(str(x))}" for x in reasons[:5])
    if anti:
        lines.append("\n⚠️ <b>Риски / анти-профили</b>")
        lines.extend(f"• {html.escape(str(x))}" for x in anti[:5])
    if not positive and not reasons and not anti:
        lines.append("Подробные причины не сохранены в payload этого сигнала. Базовые метрики доступны в карточке сигнала.")
    return "\n".join(lines)


def build_market_mood_text():
    rows = get_recent_signals(limit=30)
    if not rows:
        return "🌡 <b>MARKET MOOD</b>\n\nНедостаточно недавних сигналов."
    long_n = sum(1 for r in rows if "LONG" in _signal_direction(r))
    short_n = sum(1 for r in rows if "SHORT" in _signal_direction(r))
    avg_q = sum(_signal_quality(r) for r in rows) / max(1, len(rows))
    avg_p = sum(_signal_probability(r) for r in rows) / max(1, len(rows))
    directional = (long_n - short_n) / max(1, len(rows))
    mood = max(0, min(100, 50 + directional * 25 + (avg_q - 70) * 0.8 + (avg_p - 65) * 0.5))
    label = "Strong Bull" if mood >= 75 else "Bull" if mood >= 60 else "Neutral" if mood >= 40 else "Bear" if mood >= 25 else "Strong Bear"
    icon = "🟢" if mood >= 60 else "🟡" if mood >= 40 else "🔴"
    return (
        "🌡 <b>MARKET MOOD</b>\n\n"
        f"{icon} Индекс: <b>{mood:.0f}/100</b> — <b>{label}</b>\n"
        f"LONG / SHORT: <b>{long_n} / {short_n}</b>\n"
        f"Средний Quality: <b>{avg_q:.1f}</b>\n"
        f"Средняя Probability: <b>{avg_p:.1f}%</b>\n\n"
        "Индекс — внутренняя сводка по последним сигналам, а не отдельный прогноз цены."
    )


def build_heat_map_text():
    rows = get_recent_signals(limit=50)
    if not rows:
        return "🗺 <b>HEAT MAP</b>\n\nНедостаточно данных."
    latest = {}
    for row in rows:
        sym = _signal_symbol(row)
        if sym not in latest:
            latest[sym] = row
        if len(latest) >= 12:
            break
    lines = ["🗺 <b>HEAT MAP ПО ПОСЛЕДНИМ СИГНАЛАМ</b>", ""]
    for sym, row in latest.items():
        d = _signal_direction(row)
        arrow = "🟢 ↑" if "LONG" in d else "🔴 ↓" if "SHORT" in d else "🟡 →"
        lines.append(f"{arrow} <b>{html.escape(sym)}</b> • Q {_signal_quality(row):.0f} • P {_signal_probability(row):.0f}%")
    return "\n".join(lines)


def build_best_combos_text():
    rows = get_recent_signals(limit=100)
    counts = {}
    for row in rows:
        p = _signal_payload(row)
        hits = p.get("positiveProfileHits") or p.get("positive_profile_hits") or []
        if isinstance(hits, str): hits = [hits]
        for hit in hits:
            name = str(hit)
            counts[name] = counts.get(name, 0) + 1
    if not counts:
        return "🧩 <b>ЛУЧШИЕ КОМБИНАЦИИ</b>\n\nВ последних сигналах нет сохранённых profile hits."
    top = sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:8]
    lines = ["🧩 <b>ЧАЩЕ ВСЕГО СРАБАТЫВАЮЩИЕ ПРОФИЛИ</b>", ""]
    lines.extend(f"• <b>{html.escape(name)}</b>: {count}" for name, count in top)
    lines.append("\nДля оценки прибыльности используй статистику после накопления закрытых paper-сделок.")
    return "\n".join(lines)


def build_scanner_intelligence_text():
    from scanner_intelligence import get_last_scan_intelligence, aggregate_24h, is_previous_process_snapshot
    from trade_engine import is_trade_scan_running, get_trade_scan_runtime_state
    row = get_last_scan_intelligence()
    running = is_trade_scan_running()
    runtime = get_trade_scan_runtime_state()
    if not row:
        if running:
            return (
                "🔎 <b>SCANNER INTELLIGENCE</b>\n\n"
                "🟡 <b>Инициализация после запуска</b>\n"
                "🌍 Загружаю Multi-Exchange Universe\n"
                "🏦 Проверяю доступные биржи\n"
                "⏳ Первый полный скан ещё не завершён."
            )
        return "🔎 <b>SCANNER INTELLIGENCE</b>\n\nДанных ещё нет. Запусти торговый скан."
    st = row.get("stages") or {}
    previous_process = is_previous_process_snapshot(row)
    lines = [
        "🔎 <b>SCANNER INTELLIGENCE</b>", "",
    ]
    if running:
        owner = runtime.get('owner') or 'unknown'
        owner_text = {'monitor':'фоновый монитор','manual':'ручной скан','near-watch':'Near-Signal re-scan'}.get(owner, owner)
        processed = int(runtime.get('processed') or 0); total = int(runtime.get('total') or 0)
        phase = runtime.get('phase') or 'scan'
        lines += [
            "🟡 <b>Скан выполняется сейчас</b>",
            f"Источник: <b>{html.escape(str(owner_text))}</b> · этап: <b>{html.escape(str(phase))}</b>",
            f"Прогресс: <b>{processed}/{total}</b>" if total else "Прогресс: формируется Universe",
            "Ниже показан последний завершённый проход.", "",
        ]
    if previous_process:
        lines += [
            "☁️ <b>Восстановлено после redeploy из Supabase</b>",
            "Ниже — последний успешно завершённый скан предыдущего процесса.", "",
        ]
    lines += [
        f"Последний успешный скан: <code>{html.escape(str(row.get('runTimeUtc') or '—'))}</code>",
        f"Проверено: <b>{int(st.get('analyzed') or 0)}</b>", "",
        "<b>Воронка:</b>",
        f"• структура/status → <b>{int(st.get('status') or 0)}</b>",
        f"• Score → <b>{int(st.get('score') or 0)}</b>",
        f"• R/R → <b>{int(st.get('rr') or 0)}</b>",
        f"• Probability → <b>{int(st.get('probability') or 0)}</b>",
        f"• Quality → <b>{int(st.get('quality') or 0)}</b>",
        f"• EV → <b>{int(st.get('ev') or 0)}</b>",
        f"• ✅ сигналы → <b>{int(st.get('signals') or 0)}</b>",
    ]
    misses = row.get("nearMisses") or []
    if misses:
        lines += ["", "<b>Ближе всех:</b>"]
        for item in misses[:5]:
            lines.append(
                f"• <b>{html.escape(str(item.get('symbol') or '?'))}</b> — {html.escape(str(item.get('reason') or 'filter'))} "
                f"| P {item.get('probability') or '—'}% | Q {item.get('qualityScore') or '—'} | EV {item.get('expectedValuePct') or '—'}"
            )
    market_state = row.get("marketState") or {}
    if market_state:
        lines += ["", "<b>Состояние анализируемого рынка:</b>",
                  f"📈 LONG bias: {int(market_state.get('LONG_BIAS') or 0)} · 📉 SHORT bias: {int(market_state.get('SHORT_BIAS') or 0)} · ➖ neutral: {int(market_state.get('NO_TRADE') or 0)}"]
    dist = row.get("distributions") or {}
    qbands = dist.get("quality") or {}
    if qbands:
        lines += ["", "<b>Quality кандидатов:</b> " + " · ".join(f"{k}:{v}" for k, v in qbands.items() if v)]
    pbands = dist.get("probability") or {}
    if pbands:
        lines += ["<b>Probability:</b> " + " · ".join(f"{k}:{v}" for k, v in pbands.items() if v)]
    evbands = dist.get("ev") or {}
    if evbands:
        lines += ["<b>EV:</b> " + " · ".join(f"{k}:{v}" for k, v in evbands.items() if v)]
    recommendation = row.get("recommendation")
    if recommendation:
        lines += ["", "🧠 <b>Комментарий:</b>", html.escape(str(recommendation))]
    agg = aggregate_24h()
    if agg.get("scans"):
        lines += ["", f"<b>За 24ч:</b> {agg['scans']} сканов · {agg['analyzed']} проверок · {agg['signals']} сигналов"]
        if agg.get('analyzed'):
            base = max(1, agg['analyzed'])
            lines.append(
                "Проход: "
                f"status {100*agg['status']/base:.0f}% → score {100*agg['score']/base:.0f}% → "
                f"P {100*agg['probability']/base:.0f}% → Q {100*agg['quality']/base:.0f}% → EV {100*agg['ev']/base:.0f}%"
            )
    return "\n".join(lines)


def build_universe_dashboard_text():
    from scanner_intelligence import get_last_scan_intelligence
    latest = get_last_scan_intelligence()
    u = dict(latest.get("universe") or {}) if latest else get_last_universe_summary()
    providers = latest.get("providerStats") or {} if latest else {}
    lines = ["🌍 <b>MULTI-EXCHANGE UNIVERSE</b>", ""]
    if not u:
        return "\n".join(lines + ["Universe ещё не собран. Запусти торговый скан."])
    lines += [
        f"Бирж настроено: <b>{int(u.get('providersConfigured') or 0)}</b>",
        f"Бирж ответило: <b>{int(u.get('providersOk') or 0)}</b>",
        f"Контрактов просмотрено: <b>{int(u.get('contractsObserved') or 0)}</b>",
        f"Уникальных ликвидных символов: <b>{int(u.get('uniqueLiquidSymbols') or 0)}</b>",
        f"После coverage-фильтра: <b>{int(u.get('coverageEligibleSymbols') or 0)}</b>",
        f"⚡ Fast pool: <b>{int(u.get('fastPoolSymbols') or 0)}</b>",
        f"🧠 Deep scan за проход: <b>{int(u.get('selectedSymbols') or 0)}</b>",
        f"Минимум бирж на символ: <b>{int(u.get('minVenues') or 1)}</b>",
    ]
    buckets = u.get('selectionBuckets') or {}
    if buckets:
        labels = {'liquidity':'ликвидность','gainer':'рост','loser':'падение','coverage':'coverage','mover':'движение','liquidity_fill':'ликвидность+'}
        lines += ['', '<b>Состав Deep Scan:</b> ' + ' · '.join(f"{labels.get(k,k)} {v}" for k,v in buckets.items())]
    if providers:
        lines += ["", "<b>По биржам:</b>"]
        for name, info in providers.items():
            icon = "🟢" if info.get("ok") else "🔴"
            lines.append(
                f"{icon} {html.escape(str(name).upper())}: contracts {int(info.get('tradable') or 0)} · liquid {int(info.get('eligible') or 0)}"
            )
    lines += ["", "Coverage повышает приоритет монет, доступных сразу на нескольких биржах; сам по себе он не ослабляет Quality/EV фильтры."]
    return "\n".join(lines)


def build_near_signal_text():
    from near_signal_watchlist import get_rows
    rows = get_rows(limit=12)
    lines = [
        '🟡 <b>NEAR-SIGNAL WATCHLIST</b>', '',
        'Только кандидаты, которым не хватает <b>одного</b> фильтра и которые действительно близки к его порогу.', ''
    ]
    if not rows:
        return '\n'.join(lines + ['Сейчас реальных near-signal кандидатов нет.'])

    def _metric(value, kind):
        if value is None:
            return '—'
        try:
            value = float(value)
        except Exception:
            return '—'
        if kind == 'Probability': return f'{value:.1f}%'
        if kind in ('Quality', 'Score'): return f'{value:.1f}'
        if kind == 'EV': return f'{value:.2f}%'
        if kind == 'R/R': return f'{value:.2f}'
        return f'{value:.2f}'

    for index, row in enumerate(rows, 1):
        gate = str(row.get('missing_gate') or row.get('reason') or 'filter')
        current = row.get('current_value')
        threshold = row.get('threshold_value')
        distance = float(row.get('distance_score') or 0)
        q = row.get('quality')
        ev = row.get('ev')
        pval = row.get('probability')
        q_text = '—' if q is None else f'{float(q):.1f}'
        ev_text = '—' if ev is None else f'{float(ev):.2f}'
        lines.extend([
            f"<b>{index}. {html.escape(str(row.get('symbol') or '?'))}</b> — близость <b>{distance:.0f}%</b>",
            f"└ Не хватает: <b>{html.escape(gate)}</b> {_metric(current, gate)} → {_metric(threshold, gate)}",
            f"   P {_metric(pval, 'Probability')} · Q {q_text} · EV {ev_text}",
            ''
        ])
    lines.append('🔄 Чем выше близость, тем раньше кандидат попадает в повторный scan.')
    return '\n'.join(lines).rstrip()


def build_shadow_signals_text():
    from shadow_signals import summary
    st = summary(); counts = st.get('counts') or {}
    return (
        '👻 <b>SHADOW SIGNALS</b>\n\n'
        'Не отправляются как сделки и не влияют на Paper PnL. Нужны, чтобы понять, какие фильтры отсекают потенциально прибыльные идеи.\n\n'
        f"Ждут вход: <b>{int(counts.get('pending_entry') or 0)}</b>\n"
        f"Вход подтверждён: <b>{int(counts.get('filled') or 0)}</b>\n"
        f"Не состоялись: <b>{int(counts.get('expired') or 0)}</b>\n"
        f"Наблюдение завершено: <b>{int(counts.get('observed') or 0)}</b>\n\n"
        f"24h выборка: <b>{int(st.get('outcomes24h') or 0)}</b> · WR <b>{float(st.get('winRate24h') or 0):.1f}%</b> · Avg <b>{float(st.get('avgReturn24h') or 0):+.2f}%</b>"
    )


def scanner_intelligence_keyboard():
    return {"inline_keyboard": [
        [{"text": "🔍 Новый скан", "callback_data": "trade_scan"}],
        [{"text": "🌍 Universe", "callback_data": "universe_dashboard"}],
        [{"text": "🟡 Near Signals", "callback_data": "near_signals"}, {"text": "👻 Shadow", "callback_data": "shadow_signals"}],
        [{"text": "📊 Рынок", "callback_data": "menu_analytics"}],
        _home_row(),
    ]}


def universe_dashboard_keyboard():
    return {"inline_keyboard": [
        [{"text": "🔄 Обновить", "callback_data": "universe_dashboard"}],
        [{"text": "🏦 Биржи", "callback_data": "exchange_status"}],
        [{"text": "🔎 Сканер", "callback_data": "scanner_intelligence"}],
        _home_row(),
    ]}


def _format_provider_time(ts):
    if not ts:
        return "—"
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%H:%M:%S UTC")
    except Exception:
        return "—"


def build_exchange_status_text(active_probe=True):
    probe_rows = probe_provider_health() if active_probe else []
    probe_by_name = {row.get("provider"): row for row in probe_rows}
    rows = get_provider_health_snapshot()
    configured = [row.get("provider") for row in rows]
    lines = [
        "🏦 <b>БИРЖИ И ИСТОЧНИКИ РЫНКА</b>",
        "",
        "Universe собирается сразу с нескольких публичных futures API; данные по каждой монете берутся через fallback-цепочку.",
        f"Порядок: <b>{' → '.join(name.upper() for name in configured)}</b>",
        "",
    ]
    online = 0
    for row in rows:
        name = str(row.get("provider") or "?").upper()
        probe = probe_by_name.get(row.get("provider"))
        if probe is not None:
            ok = bool(probe.get("ok"))
            status = "online" if ok else "degraded"
            latency = probe.get("latency_ms")
        else:
            status = row.get("status") or "unknown"
            latency = None
        if status == "online":
            icon, label = "🟢", "ONLINE"
            online += 1
        elif status == "cooldown":
            icon, label = "🟠", f"COOLDOWN {int(row.get('cooldown_remaining') or 0)}s"
        elif status == "degraded":
            icon, label = "🔴", "DEGRADED"
        else:
            icon, label = "⚪", "НЕ ПРОВЕРЕНА"
        lines.append(f"{icon} <b>{name}</b> — <b>{label}</b>")
        if latency is not None:
            lines.append(f"   ↳ ping: <b>{int(latency)} ms</b>")
        if row.get("last_success_at"):
            lines.append(f"   ↳ последний успех: {_format_provider_time(row.get('last_success_at'))}")
        if int(row.get("tradable_symbols") or 0):
            eligible = int(row.get("eligible_symbols") or 0)
            lines.append(f"   ↳ USDT perpetual: <b>{int(row.get('tradable_symbols') or 0)}</b> · ликвидных: <b>{eligible}</b>")
        if row.get("error") and status != "online":
            err = html.escape(str(row.get("error"))[:180])
            lines.append(f"   ↳ ошибка: <code>{err}</code>")
        lines.append("")
    lines.append(f"Доступно сейчас: <b>{online}/{len(rows)}</b>")
    lines.append("")
    lines.append("Если первая биржа недоступна или не знает конкретный символ, клиент переключается на следующую.")
    return "\n".join(lines)


def exchange_status_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "🔄 Проверить снова", "callback_data": "exchange_status"}],
            [{"text": "📊 Аналитика", "callback_data": "menu_analytics"}],
            _home_row(),
        ]
    }


def build_paper_goal_text():
    st = _paper_snapshot()
    closed = st["closed"]
    goal = 50
    ratio = max(0, min(1, closed / goal))
    filled = int(round(ratio * 10))
    bar = "█" * filled + "░" * (10 - filled)
    roi = ((st["balance"] / st["initial"] - 1) * 100) if st["initial"] else 0
    return (
        "🏁 <b>ТЕСТ СТРАТЕГИИ: 50 PAPER-СДЕЛОК</b>\n\n"
        f"<code>{bar}</code> <b>{closed}/{goal}</b>\n"
        f"Старт: <b>${st['initial']:.2f}</b>\n"
        f"Баланс: <b>${st['balance']:.2f}</b>\n"
        f"PnL: <b>{st['pnl']:+.2f} USDT</b>\n"
        f"ROI: <b>{roi:+.2f}%</b>\n"
        f"Win rate: <b>{st['win_rate']:.1f}%</b>\n"
        f"Profit Factor: <b>{st['pf']:.2f}</b>\n\n"
        "До завершения теста параметры стратегии лучше не менять."
    )


def build_home_text():
    monitor_settings = get_monitor_settings()
    monitor_enabled = bool(monitor_settings.get("enabled"))
    scan_alive = bool(trade_scan_thread and trade_scan_thread.is_alive())
    paper = _paper_snapshot()
    recent = get_recent_signals(limit=1)
    last = recent[0] if recent else None
    if last:
        last_line = f"{html.escape(_signal_symbol(last))} {'SHORT' if 'SHORT' in _signal_direction(last) else 'LONG'} • Q {_signal_quality(last):.0f} • EV {_signal_ev(last):+.1f}%"
    else:
        last_line = "пока нет"
    return (
        "🏠 <b>CRYPTO AI</b>\n"
        "<i>Торговый ассистент • Paper Trading • Learning</i>\n\n"
        f"📡 Монитор: <b>{'🟢 ON' if monitor_enabled else '⚪ OFF'}</b>\n"
        f"🔍 Сканер: <b>{'⏳ идёт' if scan_alive else '✅ готов'}</b>\n"
        f"🧠 Chronos: <b>{_chronos_state_text()}</b>\n\n"
        f"💰 Paper баланс: <b>${paper['balance']:.2f}</b> ({paper['pnl']:+.2f})\n"
        f"📂 Открыто позиций: <b>{paper['open']}</b>\n"
        f"🏁 Тест: <b>{paper['closed']}/50</b> закрытых сделок\n\n"
        f"💎 Последний сигнал: <b>{last_line}</b>\n\n"
        "Основные действия — на кнопках ниже."
    )


new_scan_thread = None
new_scan_lock = threading.Lock()

def calculate_percent(value, total):
    if not total:
        return 0

    return round(
        value / total * 100,
        1,
    )


def build_listing_progress_message():
    try:
        database_stats = get_database_stats()
        cache_stats = get_cache_stats()

        total = int(
            database_stats.get("total") or 0
        )

        researched = int(
            database_stats.get("researched") or 0
        )

        pending = int(
            database_stats.get("pending") or 0
        )

        interesting = int(
            database_stats.get("interesting") or 0
        )

        failed = int(
            database_stats.get("failed") or 0
        )

        research_cached = int(
            cache_stats.get("research_cached") or 0
        )

        security_cached = int(
            cache_stats.get("security_cached") or 0
        )

        results_cached = int(
            cache_stats.get("results_cached") or 0
        )

        researched_percent = calculate_percent(
            researched,
            total,
        )

        interesting_percent = calculate_percent(
            interesting,
            researched,
        )

        failed_percent = calculate_percent(
            failed,
            total,
        )

        if total == 0:
            progress_bar = "░░░░░░░░░░"
        else:
            filled = round(
                researched / total * 10
            )

            filled = max(
                0,
                min(10, filled),
            )

            progress_bar = (
                "█" * filled
                + "░" * (10 - filled)
            )

        lines = []

        lines.append(
            "<b>📈 ПРОГРЕСС БАЗЫ ЛИСТИНГОВ</b>"
        )
        lines.append("")
        lines.append(
            f"<code>{progress_bar}</code> "
            f"{researched_percent}%"
        )
        lines.append("")

        lines.append(
            f"📦 Всего монет: <b>{total}</b>"
        )

        lines.append(
            f"🔬 Изучено: "
            f"<b>{researched}</b>"
        )

        lines.append(
            f"⏳ В очереди: "
            f"<b>{pending}</b>"
        )

        lines.append(
            f"⭐ Прошли фильтр: "
            f"<b>{interesting}</b>"
        )

        lines.append(
            f"❌ Ошибок: "
            f"<b>{failed}</b>"
        )

        lines.append("")
        lines.append(
            "<b>Результативность</b>"
        )

        lines.append(
            f"Интересных среди изученных: "
            f"<b>{interesting_percent}%</b>"
        )

        lines.append(
            f"Ошибок от всей базы: "
            f"<b>{failed_percent}%</b>"
        )

        lines.append("")
        lines.append(
            "<b>Кэш</b>"
        )

        lines.append(
            f"Research: "
            f"<b>{research_cached}</b>"
        )

        lines.append(
            f"Security: "
            f"<b>{security_cached}</b>"
        )

        lines.append(
            f"Последних результатов: "
            f"<b>{results_cached}</b>"
        )

        if pending > 0:
            estimated_runs = (
                pending + 99
            ) // 100

            lines.append("")
            lines.append(
                f"До завершения базы примерно "
                f"<b>{estimated_runs}</b> запусков "
                f"по 100 монет."
            )
        else:
            lines.append("")
            lines.append(
                "✅ Первичный анализ базы завершен."
            )
            lines.append(
                "Теперь будут обновляться новые "
                "листинги и наиболее интересные проекты."
            )

        return "\n".join(lines)

    except Exception as exc:
        log(
            f"Ошибка чтения статистики базы: {exc}"
        )

        return (
            "<b>❌ Ошибка статистики базы</b>\n\n"
            f"<code>{str(exc)[:500]}</code>"
        )

listing_hunter_thread = None
listing_hunter_lock = threading.Lock()

def run_new_listings_scan(chat_id):
    global new_scan_thread

    try:
        send_message(
            chat_id,
            "⏳ Обновляю базу Binance и анализирую "
            "следующую группу новых монет.\n"
            "Ранее изученные проекты повторно "
            "не запрашиваются.",
        )

        log(
            f"Запущен анализ новых листингов "
            f"chat_id={chat_id}"
        )

        result = run_incremental_listing_scan(
            deep_limit=100
        )

        report = build_new_listings_report(
            result
        )

        send_message(
            chat_id,
            report,
            reply_markup=main_keyboard(),
        )

        log(
            "Анализ новых листингов завершен: "
            f"interesting={result.get('interestingCount')}"
        )

    except Exception as exc:
        log(
            f"Ошибка анализа новых листингов: {exc}"
        )

        send_message(
            chat_id,
            "❌ Ошибка анализа новых монет:\n"
            f"<code>{str(exc)[:500]}</code>",
            reply_markup=main_keyboard(),
        )

    finally:
        with new_scan_lock:
            new_scan_thread = None


def start_new_listings_scan(chat_id):
    global new_scan_thread

    with new_scan_lock:
        if (
            new_scan_thread is not None
            and new_scan_thread.is_alive()
        ):
            send_message(
                chat_id,
                "⚠️ Анализ базы листингов уже выполняется.",
                reply_markup=main_keyboard(),
            )
            return

        new_scan_thread = threading.Thread(
            target=run_new_listings_scan,
            args=(chat_id,),
            daemon=True,
        )

        new_scan_thread.start()

    log(
        f"Запущен поток анализа базы листингов "
        f"для chat_id={chat_id}"
    )

def run_listing_hunter_task(chat_id):
    global listing_hunter_thread

    try:
        send_message(
            chat_id,
            "🚨 <b>Listing Hunter запущен</b>\n\n"
            "Проверяю официальные анонсы "
            "MEXC и KuCoin, затем запускаю "
            "Research, Tokenomics и Security.",
        )

        log(
            f"Listing Hunter запущен "
            f"для chat_id={chat_id}"
        )

        result = run_listing_hunter(
            analysis_limit=20
        )

        report = (
            build_listing_hunter_report(
                result
            )
        )

        send_message(
            chat_id,
            report,
            reply_markup=main_keyboard(),
        )

        log(
            "Listing Hunter завершён: "
            f"found={result.get('foundNow')}, "
            f"interesting="
            f"{result.get('interestingCount')}"
        )

    except Exception as exc:
        log(
            f"Listing Hunter error: {exc}"
        )

        send_message(
            chat_id,
            "❌ <b>Ошибка Listing Hunter</b>\n\n"
            f"<code>{str(exc)[:700]}</code>",
            reply_markup=main_keyboard(),
        )

    finally:
        with listing_hunter_lock:
            listing_hunter_thread = None


def start_listing_hunter(chat_id):
    global listing_hunter_thread

    with listing_hunter_lock:
        if (
            listing_hunter_thread is not None
            and listing_hunter_thread.is_alive()
        ):
            send_message(
                chat_id,
                "⚠️ Listing Hunter уже работает.",
                reply_markup=main_keyboard(),
            )
            return

        listing_hunter_thread = (
            threading.Thread(
                target=run_listing_hunter_task,
                args=(chat_id,),
                daemon=True,
            )
        )

        listing_hunter_thread.start()

    log(
        f"Запущен поток Listing Hunter "
        f"для chat_id={chat_id}"
    )

def run_early_discovery_task(chat_id):
    global early_discovery_thread

    try:
        send_message(
            chat_id,
            "🔭 <b>Early Discovery запущен</b>\n\n"
            "Проверяю CoinGecko, CoinMarketCap "
            "и официальные биржевые анонсы.",
        )

        result = run_early_discovery(
            analysis_limit=25
        )

        report = build_early_discovery_report(
            result
        )

        send_message(
            chat_id,
            report,
            reply_markup=main_keyboard(),
        )

    except Exception as exc:
        log(
            f"Early Discovery error: {exc}"
        )

        send_message(
            chat_id,
            "❌ <b>Ошибка Early Discovery</b>\n\n"
            f"<code>{str(exc)[:700]}</code>",
            reply_markup=main_keyboard(),
        )

    finally:
        with early_discovery_lock:
            early_discovery_thread = None


def start_early_discovery(chat_id):
    global early_discovery_thread

    with early_discovery_lock:
        if (
            early_discovery_thread is not None
            and early_discovery_thread.is_alive()
        ):
            send_message(
                chat_id,
                "⚠️ Early Discovery уже работает.",
                reply_markup=main_keyboard(),
            )
            return

        early_discovery_thread = threading.Thread(
            target=run_early_discovery_task,
            args=(chat_id,),
            daemon=True,
        )

        early_discovery_thread.start()

    log(
        f"Запущен Early Discovery "
        f"для chat_id={chat_id}"
    )

def run_trade_scan_task(chat_id):
    global trade_scan_thread
    try:
        send_message(chat_id, "⏳ <b>Торговый сканер запущен</b>\nПроверяю ликвидный рынок и перспективные недавние листинги.")
        result = run_trade_scan(include_watch=True, max_results=5, source='manual')
        for signal in result.get("signals", []):
            try:
                fingerprint = signal.get("fingerprint")

                if not fingerprint:
                    log(
                        "Сигнал пропущен: отсутствует fingerprint, "
                        f"symbol={signal.get('symbol')}"
                    )
                    continue

                from trade_signal_store import save_signal

                save_signal(signal, sent=False)
                cloud_id = persist_trade_signal(signal, source="manual_trade_scan")
                if not cloud_id:
                    log(f"Сигнал сохранён локально, но не подтверждён Supabase: {fingerprint}")
                upsert_watch_candidate(signal, source="manual")
                paper_result = open_paper_from_signal(signal, source="manual_trade_scan")
                if paper_result.get("status") == "opened":
                    send_message(chat_id, format_paper_open_message(paper_result))
                elif paper_result.get("status") == "pending_entry":
                    send_message(chat_id, format_paper_pending_message(paper_result))
                elif paper_result.get("status") == "missed_entry":
                    send_message(chat_id, format_paper_missed_message(paper_result.get("position") or {}, "MISSED_BREAKOUT"))
                elif paper_result.get("status") not in {"disabled", "duplicate", "symbol-already-open"}:
                    log(f"Paper trading skipped: {paper_result.get('status')} symbol={signal.get('symbol')}")

            except Exception as exc:
                log(f"Ошибка сохранения сигнала: {exc}")
        send_message(chat_id, build_trade_scan_report(result), reply_markup=trade_keyboard())
        log(f"Ручной торговый скан завершен: signals={len(result.get('signals', []))}")
    except Exception as exc:
        log(f"Ошибка торгового сканера: {exc}")
        send_message(chat_id, f"❌ <b>Ошибка торгового сканера</b>\n<code>{str(exc)[:700]}</code>", reply_markup=main_keyboard())
    finally:
        with trade_scan_lock:
            trade_scan_thread = None


def start_trade_scan(chat_id):
    global trade_scan_thread
    from trade_engine import is_trade_scan_running, get_trade_scan_runtime_state
    with trade_scan_lock:
        engine_busy = is_trade_scan_running()
        manual_busy = trade_scan_thread is not None and trade_scan_thread.is_alive()
        if engine_busy or manual_busy:
            st = get_trade_scan_runtime_state()
            owner = st.get('owner') or ('manual' if manual_busy else 'unknown')
            owner_text = 'фоновый монитор' if owner == 'monitor' else ('ручной скан' if owner == 'manual' else 'другой цикл')
            processed = int(st.get('processed') or 0)
            total = int(st.get('total') or 0)
            progress = f"\nПрогресс: <b>{processed}/{total}</b> монет" if total else ''
            send_message(
                chat_id,
                f"⏳ <b>Скан уже выполняется</b>\nИсточник: <b>{owner_text}</b>{progress}\n\nНовый проход не запускаю, чтобы не дублировать API и RAM.",
                reply_markup=trade_keyboard(),
            )
            return
        trade_scan_thread = threading.Thread(target=run_trade_scan_task, args=(chat_id,), daemon=True)
        trade_scan_thread.start()


def enable_monitor(chat_id):
    settings = set_monitor_settings(enabled=True, chat_id=chat_id)
    if trade_monitor is not None:
        trade_monitor.start()
    send_message(chat_id, build_monitor_status(settings, trade_monitor.is_alive() if trade_monitor else False), reply_markup=main_keyboard())


def disable_monitor(chat_id):
    settings = set_monitor_settings(enabled=False, chat_id=chat_id)
    send_message(chat_id, build_monitor_status(settings, trade_monitor.is_alive() if trade_monitor else False), reply_markup=main_keyboard())


def send_monitor_status(chat_id):
    settings = get_monitor_settings()
    send_message(chat_id, build_monitor_status(
        settings,
        trade_monitor.is_alive() if trade_monitor else False,
        trade_monitor.last_run if trade_monitor else None,
        trade_monitor.last_error if trade_monitor else None,
    ), reply_markup=main_keyboard())


def send_recent_signals(chat_id):
    send_message(chat_id, build_recent_signals_report(get_recent_signals(limit=5)), reply_markup=main_keyboard())


def send_watchlist(chat_id):
    send_message(chat_id, build_watchlist_report(get_watchlist(limit=10)), reply_markup=main_keyboard())


def send_trade_performance(chat_id):
    send_message(chat_id, build_performance_report(get_trade_performance()), reply_markup=main_keyboard())


def send_v8_report(chat_id, builder):
    try:
        send_message(chat_id, "⏳ Собираю данные AI Intelligence...")
        send_message(chat_id, builder(), reply_markup=main_keyboard())
    except Exception as exc:
        log(f"AI Intelligence error: {exc}")
        send_message(chat_id, f"❌ Ошибка AI Intelligence:\n<code>{str(exc)[:500]}</code>", reply_markup=main_keyboard())


def paper_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "📊 Статистика", "callback_data": "paper_status"},
                {"text": "📂 Открытые позиции", "callback_data": "paper_positions"},
            ],
            [{"text": "📜 История сделок", "callback_data": "paper_history"}],
            [
                {"text": "▶️ Paper ON", "callback_data": "paper_on"},
                {"text": "⏸ Paper OFF", "callback_data": "paper_off"},
            ],
            [{"text": "♻️ Сбросить баланс", "callback_data": "paper_reset_confirm"}],
            _back_row("menu_performance"),
            _home_row(),
        ]
    }


def build_paper_status_text():
    stats = get_paper_performance()
    account = stats.get("account") or {}
    balance = float(account.get("balance") or 0)
    initial = float(account.get("initial_balance") or 0)
    pnl = float(stats.get("net_pnl") or account.get("realized_pnl") or 0)
    enabled = str(os.getenv("PAPER_TRADING_ENABLED", "true")).lower() in {"1", "true", "yes", "on"}
    return (
        "🧪 <b>PAPER TRADING</b>\n\n"
        f"Статус: <b>{'🟢 включён' if enabled else '⚪ выключен'}</b>\n"
        f"Стартовый баланс: <b>${initial:.2f}</b>\n"
        f"Текущий баланс: <b>${balance:.2f}</b>\n"
        f"Net PnL: <b>{pnl:+.4f} USDT</b>\n"
        f"Закрытых сделок: <b>{stats.get('closed_count', 0)}</b>\n"
        f"Win rate: <b>{float(stats.get('win_rate') or 0):.2f}%</b>\n"
        f"Profit Factor: <b>{float(stats.get('profit_factor') or 0):.2f}</b>\n"
        f"Открытых позиций: <b>{len(stats.get('open_positions') or [])}</b>\n\n"
        "Плечо рассчитывается так, чтобы оценочная ликвидация находилась за стопом с заданным запасом."
    )


def build_paper_positions_text():
    rows = get_paper_positions()
    if not rows:
        return "📂 <b>ОТКРЫТЫЕ PAPER-ПОЗИЦИИ</b>\n\nОткрытых позиций нет."
    lines = ["📂 <b>ОТКРЫТЫЕ PAPER-ПОЗИЦИИ</b>", ""]
    for row in rows[:20]:
        lines.extend([
            f"<b>{html.escape(str(row.get('symbol')))}</b> {row.get('side')} • {int(row.get('leverage') or 1)}x",
            f"Margin: ${float(row.get('margin_usd') or 0):.2f} • Notional: ${float(row.get('notional_usd') or 0):.2f}",
            f"Entry <code>{float(row.get('entry_price') or 0):.8g}</code> • TP <code>{float(row.get('tp1_price') or 0):.8g}</code>",
            f"SL <code>{float(row.get('stop_price') or 0):.8g}</code> • Liq <code>{float(row.get('estimated_liquidation_price') or 0):.8g}</code>",
            "",
        ])
    return "\n".join(lines).rstrip()


def build_paper_history_text():
    rows = get_paper_trades(20)
    if not rows:
        return "📜 <b>ИСТОРИЯ PAPER-СДЕЛОК</b>\n\nЗакрытых сделок пока нет."
    lines = ["📜 <b>ИСТОРИЯ PAPER-СДЕЛОК</b>", ""]
    for row in rows:
        pnl = float(row.get("net_pnl") or 0)
        icon = "✅" if pnl > 0 else "❌"
        lines.append(
            f"{icon} <b>{html.escape(str(row.get('symbol')))}</b> {row.get('side')} • "
            f"{row.get('close_reason')} • <b>{pnl:+.4f} USDT</b> "
            f"({float(row.get('return_on_margin_pct') or 0):+.2f}%)"
        )
    return "\n".join(lines)




def _paper_trade_dt(row):
    value = row.get("closed_at") or row.get("created_at")
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _trade_metrics(rows, initial_balance=100.0):
    rows = list(rows or [])
    pnls = [float(r.get("net_pnl") or 0) for r in rows]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    net = sum(pnls)
    pf = gross_profit / gross_loss if gross_loss > 1e-12 else (999.0 if gross_profit > 0 else 0.0)
    win_rate = len(wins) / len(rows) * 100 if rows else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    fees = sum(float(r.get("fees") or 0) for r in rows)

    equity = float(initial_balance)
    peak = equity
    max_dd = 0.0
    max_dd_pct = 0.0
    best_win_streak = 0
    worst_loss_streak = 0
    win_streak = 0
    loss_streak = 0
    ordered = sorted(rows, key=lambda r: _paper_trade_dt(r) or datetime.min.replace(tzinfo=timezone.utc))
    for row in ordered:
        pnl = float(row.get("net_pnl") or 0)
        equity += pnl
        peak = max(peak, equity)
        dd = peak - equity
        dd_pct = dd / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        max_dd_pct = max(max_dd_pct, dd_pct)
        if pnl > 0:
            win_streak += 1
            loss_streak = 0
            best_win_streak = max(best_win_streak, win_streak)
        else:
            loss_streak += 1
            win_streak = 0
            worst_loss_streak = max(worst_loss_streak, loss_streak)
    return {
        "count": len(rows), "wins": len(wins), "losses": len(losses),
        "net": net, "win_rate": win_rate, "pf": pf,
        "avg_win": avg_win, "avg_loss": avg_loss, "fees": fees,
        "roi": net / initial_balance * 100 if initial_balance else 0.0,
        "max_dd": max_dd, "max_dd_pct": max_dd_pct,
        "best_win_streak": best_win_streak, "worst_loss_streak": worst_loss_streak,
    }


def build_performance_center_text():
    stats = get_paper_performance()
    account = stats.get("account") or {}
    rows = stats.get("trades") or []
    initial = float(account.get("initial_balance") or 100.0)
    m = _trade_metrics(rows, initial)
    realized_balance = initial + m["net"]
    open_count = len(stats.get("open_positions") or [])
    pf_text = "∞" if m["pf"] >= 999 else f"{m['pf']:.2f}"
    return (
        "📈 <b>РЕЗУЛЬТАТЫ СТРАТЕГИИ</b>\n\n"
        f"💰 Реализованный капитал: <b>${realized_balance:.2f}</b>\n"
        f"PnL: <b>{m['net']:+.2f} USDT</b> • ROI <b>{m['roi']:+.2f}%</b>\n"
        f"🎯 Сделок: <b>{m['count']}</b> • открыто <b>{open_count}</b>\n"
        f"✅ Win Rate: <b>{m['win_rate']:.1f}%</b> • PF <b>{pf_text}</b>\n"
        f"📉 Max DD: <b>-${m['max_dd']:.2f}</b> ({m['max_dd_pct']:.1f}%)\n"
        f"💸 Комиссии: <b>${m['fees']:.2f}</b>\n\n"
        f"Средняя прибыль: <b>{m['avg_win']:+.2f}$</b> • средний убыток: <b>{m['avg_loss']:+.2f}$</b>\n"
        f"Серии: 🟢 <b>{m['best_win_streak']}</b> / 🔴 <b>{m['worst_loss_streak']}</b>\n\n"
        "Ниже — только самые полезные разрезы для оценки текущего сетапа."
    )


def _period_rows(days=None, today=False):
    rows = get_paper_trades(1000)
    now = datetime.now(timezone.utc)
    out = []
    for row in rows:
        dt = _paper_trade_dt(row)
        if dt is None:
            continue
        if today and dt.date() != now.date():
            continue
        if days is not None and dt < now - timedelta(days=days):
            continue
        out.append(row)
    return out


def build_period_performance_text(title, rows):
    account = get_paper_performance().get("account") or {}
    initial = float(account.get("initial_balance") or 100.0)
    m = _trade_metrics(rows, initial)
    if not rows:
        return f"{title}\n\nЗакрытых сделок за этот период пока нет."
    best = max(rows, key=lambda r: float(r.get("net_pnl") or 0))
    worst = min(rows, key=lambda r: float(r.get("net_pnl") or 0))
    pf_text = "∞" if m["pf"] >= 999 else f"{m['pf']:.2f}"
    return (
        f"{title}\n\n"
        f"Сделок: <b>{m['count']}</b> • Win Rate <b>{m['win_rate']:.1f}%</b>\n"
        f"PnL: <b>{m['net']:+.2f}$</b> • PF <b>{pf_text}</b>\n"
        f"Комиссии: <b>${m['fees']:.2f}</b>\n\n"
        f"🏆 {html.escape(str(best.get('symbol') or '?'))}: <b>{float(best.get('net_pnl') or 0):+.2f}$</b>\n"
        f"🔻 {html.escape(str(worst.get('symbol') or '?'))}: <b>{float(worst.get('net_pnl') or 0):+.2f}$</b>"
    )


def build_coin_performance_text():
    rows = get_paper_trades(1000)
    if not rows:
        return "🏆 <b>РЕЗУЛЬТАТЫ ПО МОНЕТАМ</b>\n\nЗакрытых сделок пока нет."
    grouped = {}
    for row in rows:
        sym = str(row.get("symbol") or "?").upper()
        grouped.setdefault(sym, []).append(row)
    ranking = []
    for sym, trades in grouped.items():
        m = _trade_metrics(trades, 100.0)
        ranking.append((m["net"], sym, m))
    ranking.sort(reverse=True)
    lines = ["🏆 <b>РЕЗУЛЬТАТЫ ПО МОНЕТАМ</b>", "", "🟢 <b>Лучшие</b>"]
    for net, sym, m in ranking[:5]:
        lines.append(f"• <b>{html.escape(sym)}</b>: {net:+.2f}$ • {m['count']} сделок • WR {m['win_rate']:.0f}%")
    losers = sorted(ranking, key=lambda x: x[0])[:5]
    if losers and losers[0][0] < 0:
        lines.extend(["", "🔴 <b>Худшие</b>"])
        for net, sym, m in losers:
            if net >= 0:
                continue
            lines.append(f"• <b>{html.escape(sym)}</b>: {net:+.2f}$ • {m['count']} сделок • WR {m['win_rate']:.0f}%")
    return "\n".join(lines)


def _band_summary(rows, key, bands):
    result = []
    for label, low, high in bands:
        selected = []
        for row in rows:
            try:
                value = float(row.get(key))
            except (TypeError, ValueError):
                continue
            if value >= low and (high is None or value < high):
                selected.append(row)
        if selected:
            m = _trade_metrics(selected, 100.0)
            pf = "∞" if m["pf"] >= 999 else f"{m['pf']:.2f}"
            result.append(f"• <b>{label}</b>: {m['count']} • WR {m['win_rate']:.0f}% • PnL {m['net']:+.2f}$ • PF {pf}")
    return result


def build_filter_performance_text():
    rows = get_paper_trades(1000)
    if not rows:
        return "🎚 <b>ЭФФЕКТИВНОСТЬ ФИЛЬТРОВ</b>\n\nЗакрытых сделок пока нет."
    lines = ["🎚 <b>ЭФФЕКТИВНОСТЬ ФИЛЬТРОВ</b>", "", "<b>Quality</b>"]
    lines += _band_summary(rows, "quality_score", [("85+",85,None),("80–85",80,85),("75–80",75,80),("<75",-1e9,75)])
    lines += ["", "<b>Probability</b>"]
    lines += _band_summary(rows, "probability", [("80%+",80,None),("75–80%",75,80),("70–75%",70,75),("<70%",-1e9,70)])
    lines += ["", "<b>Expected Value</b>"]
    lines += _band_summary(rows, "expected_value_pct", [("5%+",5,None),("3–5%",3,5),("2–3%",2,3),("<2%",-1e9,2)])
    lines.append("\nПорог стоит менять только после достаточной выборки; сейчас цель — 50 закрытых paper-сделок.")
    return "\n".join(lines)

def handle_portfolio_command(chat_id, text):
    parts = text.strip().split()
    command = parts[0].split("@")[0].lower()
    try:
        if command == "/portfolio_add":
            if len(parts) < 3:
                raise ValueError("Формат: /portfolio_add BTC 0.1 60000")
            set_position(parts[1], float(parts[2]), float(parts[3]) if len(parts) > 3 else 0)
        elif command == "/portfolio_del":
            if len(parts) < 2:
                raise ValueError("Формат: /portfolio_del BTC")
            remove_position(parts[1])
        send_message(chat_id, portfolio_report(), reply_markup=main_keyboard())
    except Exception as exc:
        send_message(chat_id, f"❌ {str(exc)}", reply_markup=main_keyboard())


def handle_command(chat_id, text):
    command = text.strip().split()[0].lower()
    command = command.split("@")[0]

    if command in ("/start", "/help"):
        send_message(
            chat_id,
            build_home_text(),
            reply_markup=main_keyboard(),
        )
        return

    if command == "/report":
        start_report(chat_id)
        return

    if command == "/trade":
        start_trade_scan(chat_id)
        return

    if command == "/monitor_on":
        enable_monitor(chat_id)
        return

    if command == "/monitor_off":
        disable_monitor(chat_id)
        return

    if command == "/monitor_status":
        send_monitor_status(chat_id)
        return

    if command == "/signals":
        send_recent_signals(chat_id)
        return

    if command == "/watchlist":
        send_watchlist(chat_id)
        return

    if command == "/performance":
        send_trade_performance(chat_id)
        return

    if command == "/paper":
        ensure_paper_account()
        send_message(chat_id, build_paper_status_text(), reply_markup=paper_keyboard())
        return

    if command == "/server":
        from server_status import build_server_status
        send_message(chat_id, build_server_status(), reply_markup=system_keyboard())
        return

    if command == "/pro":
        send_v8_report(chat_id, build_professional_report)
        return
    if command == "/flows":
        send_v8_report(chat_id, build_capital_flow_report)
        return
    if command == "/smartmoney":
        send_v8_report(chat_id, build_smart_money_report)
        return
    if command == "/narratives":
        send_v8_report(chat_id, build_narrative_report)
        return
    if command == "/sentiment":
        send_v8_report(chat_id, build_sentiment_report)
        return
    if command == "/news":
        send_v8_report(chat_id, build_news_report)
        return
    if command in ("/portfolio", "/portfolio_add", "/portfolio_del"):
        handle_portfolio_command(chat_id, text)
        return
    if command in ("/learn", "/learnmax"):
        send_v8_report(chat_id, build_learning_report)
        return
    if command == "/modelstatus":
        send_v8_report(chat_id, build_model_status_report)
        return
    if command == "/market":
        send_v8_report(chat_id, build_market_report)
        return
    if command == "/regime":
        send_v8_report(chat_id, build_regime_report)
        return
    if command == "/confidence":
        send_v8_report(chat_id, build_confidence_report)
        return
    if command == "/features":
        send_v8_report(chat_id, build_features_report)
        return
    if command == "/health":
        send_v8_report(chat_id, build_health_report)
        return
    if command == "/topai":
        send_v8_report(chat_id, build_top_ai_report)
        return
    if command == "/aihistory":
        parts = text.strip().split(maxsplit=1)
        symbol = parts[1] if len(parts) > 1 else ""
        send_message(chat_id, build_ai_history_report(symbol), reply_markup=ai_keyboard())
        return

    if command == "/automation_status":
        send_message(chat_id, build_automation_status(automation_supervisor), reply_markup=main_keyboard())
        return

    if command == "/status":
        remove_stale_lock()

        if is_report_running():
            send_message(
                chat_id,
                "⏳ Анализ сейчас выполняется.",
            )
        else:
            send_message(
                chat_id,
                "✅ Бот готов. Активного анализа нет.",
            )
        return

    if command == "/discovery":
        start_early_discovery(
            chat_id
        )
        return

    if command == "/progress":
        progress_message = (
            build_listing_progress_message()
        )

        send_message(
            chat_id,
            progress_message,
            reply_markup=main_keyboard(),
        )

        return

    if command == "/hunter":
        start_listing_hunter(
            chat_id
        )
        return

    send_message(
        chat_id,
        "Неизвестная команда.\nИспользуй /report, /status или /help.",
    )


def process_update(update):
    callback = update.get("callback_query")

    if callback:
        callback_id = callback.get("id")
        callback_data = callback.get("data")
        callback_message = callback.get("message", {})
        chat_id = callback_message.get("chat", {}).get("id")

        log(
            f"Получен callback: "
            f"data={callback_data}, chat_id={chat_id}"
        )

        if chat_id is None:
            log("Callback без chat_id")
            return

        if not is_authorized(chat_id):
            log(
                f"Отклонен callback от chat_id={chat_id}"
            )

            try:
                telegram_request(
                    "answerCallbackQuery",
                    {
                        "callback_query_id": callback_id,
                        "text": "Нет доступа",
                        "show_alert": True,
                    },
                    timeout=10,
                )
            except Exception as exc:
                log(
                    f"Ошибка answerCallbackQuery: {exc}"
                )

            return

        try:
            telegram_request(
                "answerCallbackQuery",
                {
                    "callback_query_id": callback_id,
                },
                timeout=10,
            )
        except Exception as exc:
            log(
                f"Не удалось закрыть callback: {exc}"
            )

        if callback_data == "menu_main":
            send_message(chat_id, build_home_text(), reply_markup=main_keyboard())
            return

        if callback_data == "menu_signals":
            send_message(chat_id, "🎯 <b>СИГНАЛЫ</b>\nПоследние входы, причины выбора и результативность.", reply_markup=signals_keyboard())
            return

        if callback_data == "menu_analytics":
            send_message(chat_id, "📊 <b>АНАЛИТИКА</b>\nРынок, комбинации и статистика стратегии.", reply_markup=analytics_keyboard())
            return

        if callback_data == "menu_market":
            send_message(chat_id, "🌍 <b>РЫНОК И НОВОСТИ</b>\nДополнительные рыночные инструменты по запросу.", reply_markup=market_keyboard())
            return

        if callback_data == "menu_trade":
            send_message(chat_id, "📡 <b>МОНИТОРИНГ</b>\nУправление автоматическим и ручным сканированием.", reply_markup=trade_keyboard())
            return

        if callback_data == "menu_ai":
            send_message(chat_id, "🤖 <b>AI ЦЕНТР</b>\nChampion, Learning, Chronos и AI-диагностика.", reply_markup=ai_keyboard())
            return

        if callback_data in {"menu_performance", "menu_portfolio"}:
            send_message(chat_id, build_performance_center_text(), reply_markup=performance_keyboard())
            return

        if callback_data == "menu_system":
            send_message(chat_id, "⚙️ <b>НАСТРОЙКИ</b>\nСтратегия и технические инструменты.", reply_markup=system_keyboard())
            return

        if callback_data == "strategy_settings":
            strategy_edit_pending.pop(str(chat_id), None)
            send_message(chat_id, build_strategy_settings_text(), reply_markup=strategy_settings_keyboard())
            return

        if callback_data == "cfg_reload":
            applied = load_strategy_settings(seed_missing=True)
            send_message(
                chat_id,
                f"🔄 Настройки загружены из Supabase: <b>{len(applied)}</b>",
                reply_markup=strategy_settings_keyboard(),
            )
            return

        if callback_data and callback_data.startswith("cfg_cat:"):
            category = callback_data.split(":", 1)[1]
            if category not in CATEGORY_TITLES:
                send_message(chat_id, "⚠️ Неизвестный раздел.", reply_markup=strategy_settings_keyboard())
                return
            strategy_edit_pending.pop(str(chat_id), None)
            send_message(
                chat_id,
                build_strategy_category_text(category),
                reply_markup=strategy_category_keyboard(category),
            )
            return

        if callback_data and callback_data.startswith("cfg_edit:"):
            key = callback_data.split(":", 1)[1]
            if key not in SPEC_BY_KEY:
                send_message(chat_id, "⚠️ Неизвестный параметр.", reply_markup=strategy_settings_keyboard())
                return
            strategy_edit_pending[str(chat_id)] = key
            send_message(chat_id, build_strategy_edit_text(key), reply_markup=strategy_edit_keyboard(key))
            return

        if callback_data and callback_data.startswith("cfg_reset:"):
            key = callback_data.split(":", 1)[1]
            try:
                value = reset_strategy_setting(key, updated_by=f"telegram:{chat_id}")
                spec = SPEC_BY_KEY[key]
                text = f"✅ <b>{html.escape(spec.title)}</b> сброшен: <code>{html.escape(value)}</code>"
                keyboard = strategy_category_keyboard(spec.category)
            except Exception as exc:
                text = f"❌ Не удалось сбросить параметр: <code>{html.escape(str(exc)[:400])}</code>"
                keyboard = strategy_settings_keyboard()
            strategy_edit_pending.pop(str(chat_id), None)
            send_message(chat_id, text, reply_markup=keyboard)
            return

        if callback_data == "best_signal":
            send_message(chat_id, build_best_signal_text(), reply_markup=signals_keyboard())
            return

        if callback_data == "explain_signal":
            send_message(chat_id, build_explain_signal_text(), reply_markup=signals_keyboard())
            return

        if callback_data == "market_mood":
            send_message(chat_id, build_market_mood_text(), reply_markup=analytics_keyboard())
            return

        if callback_data == "heat_map":
            send_message(chat_id, build_heat_map_text(), reply_markup=analytics_keyboard())
            return

        if callback_data == "best_combos":
            send_message(chat_id, build_best_combos_text(), reply_markup=analytics_keyboard())
            return

        if callback_data == "scanner_intelligence":
            send_message(chat_id, build_scanner_intelligence_text(), reply_markup=scanner_intelligence_keyboard())
            return

        if callback_data == "universe_dashboard":
            send_message(chat_id, build_universe_dashboard_text(), reply_markup=universe_dashboard_keyboard())
            return

        if callback_data == "near_signals":
            send_message(chat_id, build_near_signal_text(), reply_markup=scanner_intelligence_keyboard())
            return

        if callback_data == "shadow_signals":
            send_message(chat_id, build_shadow_signals_text(), reply_markup=scanner_intelligence_keyboard())
            return

        if callback_data == "exchange_status":
            send_message(chat_id, build_exchange_status_text(active_probe=True), reply_markup=exchange_status_keyboard())
            return

        if callback_data == "perf_today":
            send_message(chat_id, build_period_performance_text("📅 <b>СЕГОДНЯ (UTC)</b>", _period_rows(today=True)), reply_markup=performance_keyboard())
            return

        if callback_data == "perf_week":
            send_message(chat_id, build_period_performance_text("📆 <b>ПОСЛЕДНИЕ 7 ДНЕЙ</b>", _period_rows(days=7)), reply_markup=performance_keyboard())
            return

        if callback_data == "perf_coins":
            send_message(chat_id, build_coin_performance_text(), reply_markup=performance_keyboard())
            return

        if callback_data == "perf_filters":
            send_message(chat_id, build_filter_performance_text(), reply_markup=performance_keyboard())
            return

        if callback_data == "paper_goal":
            send_message(chat_id, build_paper_goal_text(), reply_markup=portfolio_keyboard())
            return

        if callback_data == "dashboard":
            send_message(chat_id, build_dashboard_text(), reply_markup=dashboard_keyboard())
            return

        if callback_data == "scan_status":
            from trade_engine import is_trade_scan_running, get_trade_scan_runtime_state
            engine_busy = is_trade_scan_running()
            manual_busy = trade_scan_thread is not None and trade_scan_thread.is_alive()
            if engine_busy or manual_busy:
                st = get_trade_scan_runtime_state()
                owner = st.get('owner') or ('manual' if manual_busy else 'unknown')
                owner_text = 'фоновый монитор' if owner == 'monitor' else ('ручной скан' if owner == 'manual' else 'другой цикл')
                phase_map = {'universe':'сбор Universe', 'market_data':'загрузка рынка', 'analysis':'анализ монет', 'ranking':'AI/ранжирование'}
                phase = phase_map.get(st.get('phase'), st.get('phase') or 'работа')
                processed = int(st.get('processed') or 0)
                total = int(st.get('total') or 0)
                progress = f"\nПрогресс: <b>{processed}/{total}</b> монет" if total else ''
                text = f"⏳ <b>Сканер занят</b>\nИсточник: <b>{owner_text}</b>\nЭтап: <b>{phase}</b>{progress}"
            else:
                text = "✅ <b>Сканер готов</b>\nАктивного ручного или фонового прохода нет. Можно запустить новый поиск входов."
            send_message(chat_id, text, reply_markup=trade_keyboard())
            return

        if callback_data == "paper_menu":
            ensure_paper_account()
            send_message(chat_id, build_paper_status_text(), reply_markup=paper_keyboard())
            return

        if callback_data == "paper_status":
            send_message(chat_id, build_paper_status_text(), reply_markup=paper_keyboard())
            return

        if callback_data == "paper_positions":
            send_message(chat_id, build_paper_positions_text(), reply_markup=paper_keyboard())
            return

        if callback_data == "paper_history":
            send_message(chat_id, build_paper_history_text(), reply_markup=paper_keyboard())
            return

        if callback_data == "paper_on":
            try:
                save_strategy_setting("PAPER_TRADING_ENABLED", True, updated_by=f"telegram:{chat_id}")
                text = "🟢 <b>Paper Trading включён</b>\nНовые финальные сигналы будут открывать виртуальные позиции."
            except Exception as exc:
                text = f"❌ Ошибка: <code>{html.escape(str(exc)[:300])}</code>"
            send_message(chat_id, text, reply_markup=paper_keyboard())
            return

        if callback_data == "paper_off":
            try:
                save_strategy_setting("PAPER_TRADING_ENABLED", False, updated_by=f"telegram:{chat_id}")
                text = "⚪ <b>Paper Trading выключен</b>\nНовые позиции не открываются. Уже открытые продолжают отслеживаться."
            except Exception as exc:
                text = f"❌ Ошибка: <code>{html.escape(str(exc)[:300])}</code>"
            send_message(chat_id, text, reply_markup=paper_keyboard())
            return

        if callback_data == "paper_reset_confirm":
            send_message(
                chat_id,
                "⚠️ <b>Сбросить paper-счёт?</b>\nБудут удалены история и статистика. Сброс невозможен при открытых позициях.",
                reply_markup={"inline_keyboard": [
                    [{"text": "✅ Да, сбросить до $100", "callback_data": "paper_reset_execute"}],
                    [{"text": "❌ Отмена", "callback_data": "paper_menu"}],
                ]},
            )
            return

        if callback_data == "paper_reset_execute":
            result = reset_paper_account(100.0)
            if result.get("status") == "reset":
                text = "✅ Paper-счёт сброшен. Баланс: <b>$100.00</b>."
            elif result.get("status") == "open-positions-exist":
                text = "❌ Сначала дождись закрытия всех paper-позиций."
            else:
                text = "❌ Не удалось сбросить paper-счёт. Проверь Render logs."
            send_message(chat_id, text, reply_markup=paper_keyboard())
            return

        if callback_data == "ai_optimizer":
            send_message(chat_id, build_ai_optimizer_text(), reply_markup=ai_optimizer_keyboard())
            return

        if callback_data == "optimizer_run":
            result = run_optimizer(trigger=f"telegram:{chat_id}")
            send_message(
                chat_id,
                f"✅ <b>AI Optimizer завершён</b>\nСделок: <b>{result.get('samples',0)}</b>\nРекомендаций: <b>{result.get('recommendations_count',0)}</b>",
                reply_markup=ai_optimizer_keyboard(),
            )
            return

        if callback_data == "adaptive_train":
            result = train_candidate(trigger=f"telegram:{chat_id}")
            met = result.get('metrics') or {}
            text = (
                "🧬 <b>Adaptive Model</b>\n\n"
                f"Статус: <b>{html.escape(str(result.get('status')))}</b>\n"
                f"Версия: <code>{html.escape(str(result.get('version') or '—'))}</code>\n"
                f"Train: <b>{result.get('samples_train',0)}</b> · Validation: <b>{result.get('samples_validation',0)}</b>\n"
                f"LogLoss: <b>{_fmt_metric(met.get('log_loss'),3)}</b> · baseline: <b>{_fmt_metric(met.get('baseline_log_loss'),3)}</b>"
            )
            send_message(chat_id, text, reply_markup=ai_optimizer_keyboard())
            return

        if callback_data.startswith("opt_apply:"):
            rid = callback_data.split(":",1)[1]
            result = apply_recommendation(rid, updated_by=f"telegram:{chat_id}")
            text = "✅ Рекомендация применена." if result.get('status') == 'applied' else f"⚠️ {html.escape(str(result.get('status')))}"
            send_message(chat_id, text, reply_markup=ai_optimizer_keyboard())
            return

        if callback_data.startswith("opt_reject:"):
            rid = callback_data.split(":",1)[1]
            reject_recommendation(rid, updated_by=f"telegram:{chat_id}")
            send_message(chat_id, "✖️ Рекомендация отклонена.", reply_markup=ai_optimizer_keyboard())
            return

        if callback_data == "chronos_on":
            try:
                from chronos_forecaster import set_chronos_enabled
                enabled = set_chronos_enabled(True)
                text = (
                    "🟢 <b>Chronos включён</b>\n"
                    "Будет применяться только к финальному кандидату и только если memory guard разрешит запуск."
                    if enabled else "⚠️ Не удалось включить Chronos."
                )
            except Exception as exc:
                text = f"❌ Ошибка включения Chronos: <code>{str(exc)[:300]}</code>"
            send_message(chat_id, text, reply_markup=ai_keyboard())
            return

        if callback_data == "chronos_off":
            try:
                from chronos_forecaster import set_chronos_enabled
                set_chronos_enabled(False)
                text = "⚪ <b>Chronos выключен</b>\nСканирование продолжит работать без модели Chronos."
            except Exception as exc:
                text = f"❌ Ошибка выключения Chronos: <code>{str(exc)[:300]}</code>"
            send_message(chat_id, text, reply_markup=ai_keyboard())
            return

        if callback_data == "chronos_status":
            send_message(
                chat_id,
                f"🧠 <b>Chronos</b>\nТекущее состояние: <b>{_chronos_state_text()}</b>\n\n"
                "Настройка сохраняется для текущего экземпляра Render и после рестарта возвращается "
                "к значению CHRONOS_ENABLED из ENV.",
                reply_markup=ai_keyboard(),
            )
            return

        if callback_data == "model_status":
            send_v8_report(chat_id, build_model_status_report)
            return

        if callback_data == "portfolio_help":
            send_message(
                chat_id,
                "💼 <b>Управление портфелем</b>\n\n"
                "Добавить: <code>/portfolio_add BTC 0.1 60000</code>\n"
                "Удалить: <code>/portfolio_del BTC</code>\n"
                "Показать: <code>/portfolio</code>",
                reply_markup=portfolio_keyboard(),
            )
            return

        if callback_data == "health_check":
            try:
                from healthcheck import main as run_healthcheck
                result = run_healthcheck()
                text = "✅ <b>Health Check пройден</b>" if result == 0 else "❌ <b>Health Check не пройден</b>"
            except Exception as exc:
                text = f"❌ <b>Health Check error</b>\n<code>{str(exc)[:500]}</code>"
            send_message(chat_id, text, reply_markup=system_keyboard())
            return

        if callback_data == "trade_scan":
            start_trade_scan(chat_id)
            return

        if callback_data == "monitor_on":
            enable_monitor(chat_id)
            return

        if callback_data == "monitor_off":
            disable_monitor(chat_id)
            return

        if callback_data == "monitor_status":
            send_monitor_status(chat_id)
            return

        if callback_data == "recent_signals":
            send_recent_signals(chat_id)
            return

        if callback_data == "trade_watchlist":
            send_watchlist(chat_id)
            return

        if callback_data == "trade_performance":
            send_trade_performance(chat_id)
            return

        if callback_data == "top_ai":
            send_v8_report(chat_id, build_top_ai_report)
            return
        if callback_data == "ai_history":
            send_message(chat_id, build_ai_history_report(), reply_markup=ai_keyboard())
            return
        if callback_data == "pro_report":
            send_v8_report(chat_id, build_professional_report)
            return
        if callback_data == "capital_flows":
            send_v8_report(chat_id, build_capital_flow_report)
            return
        if callback_data == "smart_money":
            send_v8_report(chat_id, build_smart_money_report)
            return
        if callback_data == "narratives":
            send_v8_report(chat_id, build_narrative_report)
            return
        if callback_data == "ai_news":
            send_v8_report(chat_id, build_news_report)
            return
        if callback_data == "sentiment":
            send_v8_report(chat_id, build_sentiment_report)
            return
        if callback_data == "portfolio":
            send_v8_report(chat_id, portfolio_report)
            return
        if callback_data == "self_learning":
            send_v8_report(chat_id, build_learning_report)
            return

        if callback_data == "automation_status":
            send_message(chat_id, build_automation_status(automation_supervisor), reply_markup=main_keyboard())
            return

        if callback_data == "run_report":
            log("Нажата кнопка обычного отчета")
            start_report(chat_id)
            return

        if callback_data == "scan_new_100":
            log(
                "Нажата кнопка обновления базы листингов"
            )
            start_new_listings_scan(chat_id)
            return

        if callback_data == "early_discovery":
            log(
                "Нажата кнопка Early Discovery"
            )

            start_early_discovery(
                chat_id
            )

            return

        if callback_data == "listing_hunter":
            log(
                "Нажата кнопка Listing Hunter"
            )

            start_listing_hunter(
                chat_id
            )

            return

        if callback_data == "listing_progress":
            log(
                "Нажата кнопка прогресса базы"
            )

            progress_message = (
                build_listing_progress_message()
            )

            send_message(
                chat_id,
                progress_message,
                reply_markup=main_keyboard(),
            )

            return

        if callback_data == "server_status":
            from server_status import build_server_status
            send_message(chat_id, build_server_status(), reply_markup=system_keyboard())
            return

        if callback_data == "bot_status":
            log("Нажата кнопка статуса")

            if is_report_running():
                status_text = (
                    "⏳ Обычный анализ выполняется."
                )
            elif (
                new_scan_thread is not None
                and new_scan_thread.is_alive()
            ):
                status_text = (
                    "⏳ Анализ базы листингов выполняется."
                )
            elif trade_scan_thread is not None and trade_scan_thread.is_alive():
                status_text = "⏳ Торговый сканер выполняется."
            else:
                monitor_settings = get_monitor_settings()
                monitor_text = "включен" if monitor_settings.get("enabled") else "остановлен"
                status_text = f"✅ Бот готов. Фоновый мониторинг: {monitor_text}."

            send_message(
                chat_id,
                status_text,
                reply_markup=main_keyboard(),
            )
            return

        log(
            f"Неизвестный callback_data: "
            f"{callback_data}"
        )

        send_message(
            chat_id,
            (
                "⚠️ Кнопка устарела.\n"
                "Отправь /start, чтобы обновить меню."
            ),
            reply_markup=main_keyboard(),
        )
        return

    message = update.get("message")

    if not message:
        return

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = message.get("text", "")

    if chat_id is None or not text:
        return

    if not is_authorized(chat_id):
        log(
            f"Отклонена команда от chat_id={chat_id}"
        )

        try:
            send_message(
                chat_id,
                "⛔ У тебя нет доступа к этому боту.",
            )
        except Exception:
            pass

        return

    pending_key = strategy_edit_pending.get(str(chat_id))
    if pending_key:
        if text.strip().lower() == "/cancel":
            spec = SPEC_BY_KEY[pending_key]
            strategy_edit_pending.pop(str(chat_id), None)
            send_message(
                chat_id,
                "↩️ Изменение отменено.",
                reply_markup=strategy_category_keyboard(spec.category),
            )
            return
        try:
            value = save_strategy_setting(
                pending_key,
                text.strip(),
                updated_by=f"telegram:{chat_id}",
            )
            spec = SPEC_BY_KEY[pending_key]
            strategy_edit_pending.pop(str(chat_id), None)
            send_message(
                chat_id,
                f"✅ <b>{html.escape(spec.title)}</b> обновлён: <code>{html.escape(value)}</code>\n"
                "Новое значение применяется к следующим расчётам.",
                reply_markup=strategy_category_keyboard(spec.category),
            )
        except Exception as exc:
            send_message(
                chat_id,
                f"❌ Некорректное значение: <code>{html.escape(str(exc)[:400])}</code>\n\n"
                "Попробуй ещё раз или отправь /cancel.",
                reply_markup=strategy_edit_keyboard(pending_key),
            )
        return

    if text.startswith("/"):
        log(
            f"Получена команда {text!r} "
            f"от chat_id={chat_id}"
        )
        handle_command(chat_id, text)
def start_runtime_services():
    """Initialize stores and background workers without starting long polling."""
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN отсутствует в переменных окружения")
    if not ALLOWED_CHAT_ID:
        raise RuntimeError("TELEGRAM_CHAT_ID отсутствует в переменных окружения")

    remove_stale_lock()
    set_bot_commands()
    load_strategy_settings(seed_missing=True)
    initialize_signal_store()
    ensure_paper_account()

    # Render can restart with an empty ephemeral SQLite volume. When enabled,
    # restore monitoring automatically using TELEGRAM_CHAT_ID.
    auto_monitor = os.getenv("MONITOR_AUTO_ENABLE", "true").strip().lower() in {"1", "true", "yes", "on"}
    settings = get_monitor_settings()
    if auto_monitor and (not settings.get("enabled") or not settings.get("chat_id")):
        settings = set_monitor_settings(enabled=True, chat_id=ALLOWED_CHAT_ID)
        log("Trade Monitor автоматически восстановлен после запуска runtime.")

    global trade_monitor, automation_supervisor, runtime_health_monitor
    if trade_monitor is None:
        trade_monitor = TradeMonitor(send_message, log)
        if settings.get("enabled"):
            trade_monitor.start()

    if automation_supervisor is None:
        automation_supervisor = AutomationSupervisor(send_message, log, ALLOWED_CHAT_ID)
        automation_supervisor.start()

    if runtime_health_monitor is None and os.getenv("HEALTH_MONITOR_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}:
        from runtime_health import RuntimeHealthMonitor
        runtime_health_monitor = RuntimeHealthMonitor(
            log,
            lambda: trade_monitor,
            lambda: automation_supervisor,
        )
        runtime_health_monitor.start()

    log("Telegram runtime services запущены в webhook-режиме.")
    return automation_supervisor


def stop_runtime_services():
    global trade_monitor, automation_supervisor, runtime_health_monitor
    if runtime_health_monitor is not None:
        try:
            runtime_health_monitor.stop()
        finally:
            runtime_health_monitor = None
    if trade_monitor is not None:
        try:
            trade_monitor.stop()
        finally:
            trade_monitor = None
    if automation_supervisor is not None:
        try:
            automation_supervisor.stop()
        finally:
            automation_supervisor = None
    log("Telegram runtime services остановлены.")


def listen():
    if not BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN отсутствует в .env"
        )

    if not ALLOWED_CHAT_ID:
        raise RuntimeError(
            "TELEGRAM_CHAT_ID отсутствует в .env"
        )

    remove_stale_lock()
    delete_webhook()
    set_bot_commands()
    initialize_signal_store()
    ensure_paper_account()

    global trade_monitor, automation_supervisor
    auto_monitor = os.getenv("MONITOR_AUTO_ENABLE", "true").strip().lower() in {"1", "true", "yes", "on"}
    settings = get_monitor_settings()
    if auto_monitor and (not settings.get("enabled") or not settings.get("chat_id")):
        settings = set_monitor_settings(enabled=True, chat_id=ALLOWED_CHAT_ID)
        log("Trade Monitor автоматически восстановлен после запуска polling runtime.")
    trade_monitor = TradeMonitor(send_message, log)
    if settings.get("enabled"):
        trade_monitor.start()

    automation_supervisor = AutomationSupervisor(send_message, log, ALLOWED_CHAT_ID)
    automation_supervisor.start()

    offset = None

    log("Telegram command bot запущен.")
    log(
        f"Разрешенный chat_id: "
        f"{ALLOWED_CHAT_ID}"
    )

    while True:
        try:
            payload = {
                "timeout": POLL_TIMEOUT,
                "allowed_updates": [
                    "message",
                    "callback_query",
                ],
            }

            if offset is not None:
                payload["offset"] = offset

            updates = telegram_request(
                "getUpdates",
                payload,
                timeout=POLL_TIMEOUT + 10,
            )

            for update in updates:
                offset = update["update_id"] + 1
                process_update(update)

        except requests.RequestException as exc:
            log(
                f"Ошибка соединения Telegram: {exc}"
            )
            time.sleep(RETRY_DELAY)

        except KeyboardInterrupt:
            if trade_monitor is not None:
                trade_monitor.stop()
            if automation_supervisor is not None:
                automation_supervisor.stop()
            log(
                "Telegram command bot "
                "остановлен пользователем."
            )
            break

        except Exception as exc:
            log(
                f"Ошибка Telegram listener: {exc}"
            )
            time.sleep(RETRY_DELAY)


if __name__ == "__main__":
    listen()
