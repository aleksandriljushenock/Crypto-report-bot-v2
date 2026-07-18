import os
import subprocess
import sys
import threading
import time
import re
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
from trade_outcome_tracker import get_trade_performance, register_trade_signal
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
cloud_store = CloudLearningStore()


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

def _back_row():
    return [{"text": "⬅️ Главное меню", "callback_data": "menu_main"}]


def main_keyboard():
    """Compact v11 navigation grouped by user intent."""
    return {
        "inline_keyboard": [
            [
                {"text": "📊 Анализ рынка", "callback_data": "menu_market"},
                {"text": "📈 Торговля", "callback_data": "menu_trade"},
            ],
            [
                {"text": "🧠 AI Intelligence", "callback_data": "menu_ai"},
                {"text": "💼 Портфель", "callback_data": "menu_portfolio"},
            ],
            [{"text": "⚙️ Система", "callback_data": "menu_system"}],
        ]
    }


def market_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "📊 Market Report", "callback_data": "run_report"}],
            [
                {"text": "🔥 Сигналы", "callback_data": "recent_signals"},
                {"text": "🔍 Найти входы", "callback_data": "trade_scan"},
            ],
            [
                {"text": "💰 Capital Flow", "callback_data": "capital_flows"},
                {"text": "🧠 Нарративы", "callback_data": "narratives"},
            ],
            [
                {"text": "📰 Новости", "callback_data": "ai_news"},
                {"text": "😨 Fear & Greed", "callback_data": "sentiment"},
            ],
            _back_row(),
        ]
    }


def trade_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "⭐ Watchlist", "callback_data": "trade_watchlist"},
                {"text": "📈 Результаты", "callback_data": "trade_performance"},
            ],
            [
                {"text": "▶️ Trade Monitor", "callback_data": "monitor_on"},
                {"text": "⏸ Остановить", "callback_data": "monitor_off"},
            ],
            [{"text": "📡 Статус мониторинга", "callback_data": "monitor_status"}],
            [
                {"text": "🔭 Early Discovery", "callback_data": "early_discovery"},
                {"text": "🚨 Listing Hunter", "callback_data": "listing_hunter"},
            ],
            _back_row(),
        ]
    }


def ai_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "🏆 TOP AI", "callback_data": "top_ai"}],
            [{"text": "🚀 AI Центр", "callback_data": "pro_report"}],
            [
                {"text": "🐋 Smart Money", "callback_data": "smart_money"},
                {"text": "🧬 AI Learning", "callback_data": "self_learning"},
            ],
            [{"text": "📚 AI History", "callback_data": "ai_history"}],
            _back_row(),
        ]
    }


def portfolio_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "💼 Обзор портфеля", "callback_data": "portfolio"}],
            [{"text": "ℹ️ Управление позициями", "callback_data": "portfolio_help"}],
            _back_row(),
        ]
    }


def system_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Статус бота", "callback_data": "bot_status"},
                {"text": "🧩 Сервисы", "callback_data": "automation_status"},
            ],
            [{"text": "🆕 Обновить базу листингов", "callback_data": "scan_new_100"}],
            [{"text": "📈 Прогресс базы", "callback_data": "listing_progress"}],
            [{"text": "❤️ Health Check", "callback_data": "health_check"}],
            _back_row(),
        ]
    }


def build_home_text():
    monitor_settings = get_monitor_settings()
    monitor_enabled = bool(monitor_settings.get("enabled"))
    monitor_state = "🟢 включен" if monitor_enabled else "⚪ остановлен"

    running = []
    if is_report_running():
        running.append("Market Report")
    if new_scan_thread is not None and new_scan_thread.is_alive():
        running.append("Listing DB")
    if trade_scan_thread is not None and trade_scan_thread.is_alive():
        running.append("Trade Scan")

    activity = ", ".join(running) if running else "нет активных задач"
    return (
        "🤖 <b>Crypto Intelligence Platform v12</b>\n\n"
        f"📡 Trade Monitor: <b>{monitor_state}</b>\n"
        f"⚙️ Активность: <b>{activity}</b>\n"
        f"🕒 Обновлено: <b>{datetime.now().strftime('%H:%M:%S')}</b>\n\n"
        "Выбери раздел:"
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
                register_trade_signal(signal)
                upsert_watch_candidate(signal, source="manual")

                cloud_store.save(
                    {
                        "symbol": signal.get("symbol"),
                        "timeframe": signal.get("timeframe") or "manual",
                        "signal_type": "manual_trade_scan",
                        "signal_direction": signal.get("direction"),
                        "signal_score": signal.get("score"),
                        "signal_confidence": signal.get("probability"),
                        "entry_price": signal.get("entryPrice"),
                        "target_price": signal.get("tp1"),
                        "stop_loss": signal.get("stop"),
                        "market_price_at_signal": signal.get("entryPrice"),
                        "features": to_json_safe(signal),
                        "metadata": {
                            "source": "telegram_manual_trade",
                            "fingerprint": fingerprint,
                            "ai_score": signal.get("aiScore"),
                            "ai_tier": signal.get("aiTier"),
                            "tp2": signal.get("tp2"),
                            "tp3": signal.get("tp3"),
                        },
                        "signal_created_at": datetime.now(timezone.utc).isoformat(),
                        "training_status": "pending",
                    }
                )

            except Exception as exc:
                log(f"Ошибка сохранения сигнала: {exc}")
        send_message(chat_id, build_trade_scan_report(result), reply_markup=main_keyboard())
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
            send_message(chat_id, "⚠️ Торговый скан уже выполняется.", reply_markup=main_keyboard())
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

    if text.startswith("/"):
        log(
            f"Получена команда {text!r} "
            f"от chat_id={chat_id}"
        )
        handle_command(chat_id, text)
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

    global trade_monitor, automation_supervisor
    trade_monitor = TradeMonitor(send_message, log)
    if get_monitor_settings().get("enabled"):
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