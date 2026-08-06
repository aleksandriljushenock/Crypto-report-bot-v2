import os
import subprocess
import sys
import threading
import time
import re
import html
from datetime import datetime, timezone
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
    """User-first navigation with quick actions and preserved legacy tools."""
    return {
        "inline_keyboard": [
            [{"text": "⚡ Запустить торговый скан", "callback_data": "trade_scan"}],
            [
                {"text": "📈 Торговый центр", "callback_data": "menu_trade"},
                {"text": "🧠 AI-центр", "callback_data": "menu_ai"},
            ],
            [
                {"text": "🌍 Рынок", "callback_data": "menu_market"},
                {"text": "🔭 Поиск возможностей", "callback_data": "menu_discovery"},
            ],
            [
                {"text": "💼 Портфель", "callback_data": "menu_portfolio"},
                {"text": "⚙️ Система", "callback_data": "menu_system"},
            ],
            [{"text": "📟 Панель состояния", "callback_data": "dashboard"}],
        ]
    }


def market_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "📊 Полный Market Report", "callback_data": "run_report"}],
            [
                {"text": "🔥 Последние сигналы", "callback_data": "recent_signals"},
                {"text": "💰 Capital Flow", "callback_data": "capital_flows"},
            ],
            [
                {"text": "🧠 Нарративы", "callback_data": "narratives"},
                {"text": "😨 Fear & Greed", "callback_data": "sentiment"},
            ],
            [{"text": "📰 Новости рынка", "callback_data": "ai_news"}],
            _home_row(),
        ]
    }


def trade_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "⚡ Запустить скан сейчас", "callback_data": "trade_scan"}],
            [
                {"text": "▶️ Включить монитор", "callback_data": "monitor_on"},
                {"text": "⏸ Остановить монитор", "callback_data": "monitor_off"},
            ],
            [
                {"text": "📡 Статус мониторинга", "callback_data": "monitor_status"},
                {"text": "⏱ Статус скана", "callback_data": "scan_status"},
            ],
            [
                {"text": "⭐ Watchlist", "callback_data": "trade_watchlist"},
                {"text": "📈 Результаты", "callback_data": "trade_performance"},
            ],
            [{"text": "🔥 Последние сигналы", "callback_data": "recent_signals"}],
            [{"text": "🧪 Paper Trading", "callback_data": "paper_menu"}],
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
            _home_row(),
        ]
    }


def ai_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "🏆 TOP AI", "callback_data": "top_ai"},
                {"text": "🚀 Проф. отчёт", "callback_data": "pro_report"},
            ],
            [
                {"text": "🐋 Smart Money", "callback_data": "smart_money"},
                {"text": "🧬 AI Learning", "callback_data": "self_learning"},
            ],
            [
                {"text": "📚 AI History", "callback_data": "ai_history"},
                {"text": "🧩 Модель", "callback_data": "model_status"},
            ],
            [
                {"text": "🟢 Chronos ON", "callback_data": "chronos_on"},
                {"text": "⚪ Chronos OFF", "callback_data": "chronos_off"},
            ],
            [{"text": "🧠 Статус Chronos", "callback_data": "chronos_status"}],
            _home_row(),
        ]
    }


def portfolio_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "💼 Обзор портфеля", "callback_data": "portfolio"}],
            [{"text": "ℹ️ Как управлять позициями", "callback_data": "portfolio_help"}],
            _home_row(),
        ]
    }


def system_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Статус бота", "callback_data": "bot_status"},
                {"text": "🧩 Фоновые сервисы", "callback_data": "automation_status"},
            ],
            [
                {"text": "❤️ Health Check", "callback_data": "health_check"},
                {"text": "📟 Панель состояния", "callback_data": "dashboard"},
            ],
            [{"text": "🎛 Настройки стратегии", "callback_data": "strategy_settings"}],
            [{"text": "📈 Прогресс базы", "callback_data": "listing_progress"}],
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


def build_dashboard_text():
    monitor_settings = get_monitor_settings()
    monitor_enabled = bool(monitor_settings.get("enabled"))
    monitor_alive = bool(trade_monitor and trade_monitor.is_alive())
    scan_alive = bool(trade_scan_thread and trade_scan_thread.is_alive())
    report_alive = bool(is_report_running())
    listing_alive = bool(new_scan_thread and new_scan_thread.is_alive())
    services = []
    if scan_alive:
        services.append("торговый скан")
    if report_alive:
        services.append("market report")
    if listing_alive:
        services.append("база листингов")
    active = ", ".join(services) if services else "нет тяжёлых задач"
    return (
        "📟 <b>ПАНЕЛЬ СОСТОЯНИЯ</b>\n\n"
        f"📡 Монитор: <b>{'включён' if monitor_enabled else 'остановлен'}</b> "
        f"({'процесс активен' if monitor_alive else 'процесс не активен'})\n"
        f"⚡ Ручной скан: <b>{'выполняется' if scan_alive else 'готов'}</b>\n"
        f"🧠 Chronos: <b>{_chronos_state_text()}</b>\n"
        f"⚙️ Активность: <b>{active}</b>\n"
        f"🕒 Обновлено: <b>{datetime.now().strftime('%H:%M:%S')}</b>\n\n"
        "Быстрые действия доступны кнопками ниже."
    )


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


def build_home_text():
    monitor_settings = get_monitor_settings()
    monitor_enabled = bool(monitor_settings.get("enabled"))
    running = []
    if is_report_running():
        running.append("Market Report")
    if new_scan_thread is not None and new_scan_thread.is_alive():
        running.append("Listing DB")
    if trade_scan_thread is not None and trade_scan_thread.is_alive():
        running.append("Trade Scan")
    activity = ", ".join(running) if running else "готов к работе"
    return (
        "🤖 <b>CRYPTO AI COMMAND CENTER</b>\n"
        "<i>AI Hedge Fund • Learning MAX • Chronos</i>\n\n"
        f"📡 Монитор сигналов: <b>{'🟢 включён' if monitor_enabled else '⚪ остановлен'}</b>\n"
        f"🧠 Chronos: <b>{_chronos_state_text()}</b>\n"
        f"⚙️ Состояние: <b>{activity}</b>\n"
        f"🕒 <b>{datetime.now().strftime('%H:%M:%S')}</b>\n\n"
        "Нажми <b>«Запустить торговый скан»</b> для немедленного поиска входов."
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
        result = run_trade_scan(include_watch=True, max_results=5)
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
    with trade_scan_lock:
        if trade_scan_thread is not None and trade_scan_thread.is_alive():
            send_message(chat_id, "⚠️ Торговый скан уже выполняется. Открой «Статус скана».", reply_markup=trade_keyboard())
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
            _back_row("menu_trade"),
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

        if callback_data == "menu_market":
            send_message(chat_id, "📊 <b>Анализ рынка</b>\nВыбери инструмент:", reply_markup=market_keyboard())
            return

        if callback_data == "menu_trade":
            send_message(chat_id, "📈 <b>Торговля</b>\nСигналы, мониторинг и листинги:", reply_markup=trade_keyboard())
            return

        if callback_data == "menu_ai":
            send_message(chat_id, "🧠 <b>AI Intelligence</b>\nПрофессиональная аналитика:", reply_markup=ai_keyboard())
            return

        if callback_data == "menu_portfolio":
            send_message(chat_id, "💼 <b>Портфель</b>\nОбзор и управление позициями:", reply_markup=portfolio_keyboard())
            return

        if callback_data == "menu_system":
            send_message(chat_id, "⚙️ <b>Система</b>\nСостояние и обслуживание:", reply_markup=system_keyboard())
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

        if callback_data == "dashboard":
            send_message(chat_id, build_dashboard_text(), reply_markup=dashboard_keyboard())
            return

        if callback_data == "scan_status":
            if trade_scan_thread is not None and trade_scan_thread.is_alive():
                text = "⏳ <b>Торговый скан выполняется</b>\nДождись итогового отчёта — новый запуск сейчас не требуется."
            else:
                text = "✅ <b>Сканер готов</b>\nМожно запустить новый поиск входов."
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