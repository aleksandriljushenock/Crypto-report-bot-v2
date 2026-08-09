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
import telegram_ui.keyboards as ui_keyboards
from telegram_ui.client import telegram_request as _tg_request, send_message as _tg_send_message, split_telegram_message as _tg_split, set_webhook as _tg_set_webhook, delete_webhook as _tg_delete_webhook, get_webhook_info as _tg_get_webhook_info
from telegram_ui.commands import register_bot_commands
from telegram_ui.status_view import render_dashboard
from telegram_ui.renderers import (
    build_ai_optimizer_text, build_strategy_settings_text, build_strategy_category_text, build_strategy_edit_text,
    build_best_signal_text, build_explain_signal_text, build_market_mood_text, build_heat_map_text, build_best_combos_text,
    build_scanner_intelligence_text, build_universe_dashboard_text, build_near_signal_text, build_shadow_signals_text,
    build_exchange_status_text, build_paper_goal_text, build_paper_status_text, build_paper_positions_text, build_paper_history_text,
    build_performance_center_text, build_period_performance_text, build_coin_performance_text, build_filter_performance_text,
    _paper_snapshot, _signal_direction, _signal_symbol, _signal_quality, _signal_ev, _trade_metrics, _paper_trade_dt, _band_summary, _period_rows, _num, _fmt_metric,
)
from telegram_ui.system_handlers import handle as handle_system_callback
from application.system_service import runtime_context
from application.diagnostics_service import snapshot_report as diagnostics_snapshot
from telegram_ui.diagnostics import render_diagnostics
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
from core.logging_setup import get_logger
from core.settings import settings
from core.runtime_config import boolean, integer, number, string, scanner_config
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
    return _tg_request(method, payload, timeout)


def split_telegram_message(text, limit=3900):
    return _tg_split(text, limit)


def send_message(chat_id, text, reply_markup=None, parse_mode="HTML"):
    return _tg_send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)


def delete_webhook():
    try:
        _tg_delete_webhook(False)
        log("Webhook удален. Long polling включен.")
    except Exception as exc:
        log(f"Не удалось удалить webhook: {exc}")


def set_webhook(webhook_url, secret_token=None, drop_pending_updates=False):
    result = _tg_set_webhook(webhook_url, secret_token=secret_token, drop_pending_updates=drop_pending_updates)
    log(f"Telegram webhook установлен: {str(webhook_url).rstrip('/')}")
    return result


def get_webhook_info():
    return _tg_get_webhook_info()


def set_bot_commands():
    try:
        register_bot_commands()
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
    return ui_keyboards.back_row(target)
def _home_row():
    return ui_keyboards.home_row()
def main_keyboard():
    return ui_keyboards.main_keyboard()
def signals_keyboard():
    return ui_keyboards.signals_keyboard()
def market_keyboard():
    return ui_keyboards.market_keyboard()
def trade_keyboard():
    return ui_keyboards.trade_keyboard()
def discovery_keyboard():
    return ui_keyboards.discovery_keyboard()
def ai_keyboard():
    return ui_keyboards.ai_keyboard()




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
    return ui_keyboards.performance_keyboard()
def portfolio_keyboard():
    """Backward-compatible alias for old callbacks."""
    return performance_keyboard()


def analytics_keyboard():
    return ui_keyboards.analytics_keyboard()
def system_keyboard():
    return ui_keyboards.system_keyboard()
def strategy_settings_keyboard():
    return ui_keyboards.strategy_settings_keyboard()


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
        enabled = boolean("CHRONOS_ENABLED", False)
    mode = string("CHRONOS_MODE", "subprocess", strategy=False).lower()
    return f"{'🟢' if enabled else '⚪'} {'включён' if enabled else 'выключен'} ({mode})"


def build_dashboard_text():
    monitor_settings = get_monitor_settings()
    ctx = runtime_context(
        monitor_enabled=bool(monitor_settings.get("enabled")),
        monitor_alive=bool(trade_monitor and trade_monitor.is_alive()),
        manual_thread_alive=bool(trade_scan_thread and trade_scan_thread.is_alive()),
        report_alive=bool(is_report_running()),
        listing_alive=bool(globals().get("new_scan_thread") and globals()["new_scan_thread"].is_alive()),
    )
    return render_dashboard(ctx, _chronos_state_text())

def dashboard_keyboard():
    return ui_keyboards.dashboard_keyboard()


































def scanner_intelligence_keyboard():
    return ui_keyboards.scanner_intelligence_keyboard()
def universe_dashboard_keyboard():
    return ui_keyboards.universe_dashboard_keyboard()




def exchange_status_keyboard():
    return ui_keyboards.exchange_status_keyboard()


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
    return ui_keyboards.paper_keyboard()























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
        monitor_settings = get_monitor_settings()
        ctx = runtime_context(
            monitor_enabled=bool(monitor_settings.get("enabled")),
            monitor_alive=bool(trade_monitor is not None and trade_monitor.is_running()),
            manual_thread_alive=bool(trade_scan_thread is not None and trade_scan_thread.is_alive()),
            report_alive=bool(is_report_running()),
            listing_alive=bool(new_scan_thread is not None and new_scan_thread.is_alive()),
        )
        send_message(chat_id, render_dashboard(ctx, _chronos_state_text()), reply_markup=dashboard_keyboard())
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
    # Lazy import avoids circular dependency while keeping polling/webhook entrypoints stable.
    import sys
    from telegram_ui.router import process_update as route_update
    return route_update(update, sys.modules[__name__])
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
    auto_monitor = boolean("MONITOR_AUTO_ENABLE", True, strategy=False)
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

    if runtime_health_monitor is None and boolean("HEALTH_MONITOR_ENABLED", True, strategy=False):
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
    auto_monitor = boolean("MONITOR_AUTO_ENABLE", True, strategy=False)
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
