import os
from core.runtime_config import boolean, integer, number, near_signal_config
import threading
import time
from datetime import datetime, timezone

from trade_engine import run_trade_scan
from trade_signal_report import build_signal_block
from trade_outcome_tracker import persist_trade_signal
from trade_watchlist import upsert_watch_candidate
from trade_signal_store import (
    get_monitor_settings,
    initialize_signal_store,
    mark_signal_sent,
    save_signal,
    signal_recently_sent,
)


class TradeMonitor:
    def __init__(self, sender, logger):
        self.sender = sender
        self.logger = logger
        self._thread = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self.last_run = None
        self.last_error = None
        self.restart_count = 0
        self.heartbeat_at = None
        initialize_signal_store()

    def is_alive(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        with self._lock:
            if self.is_alive():
                return False
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._supervised_loop, daemon=True, name='trade-monitor')
            self._thread.start()
            return True

    def is_running(self):
        return bool(self._thread is not None and self._thread.is_alive() and not self._stop_event.is_set())

    def stop(self):
        self._stop_event.set()

    def _supervised_loop(self):
        """Keep the monitor alive even if a cycle raises an unexpected exception."""
        self.logger('Фоновый торговый монитор запущен.')
        while not self._stop_event.is_set():
            try:
                self._loop()
            except BaseException as exc:
                if self._stop_event.is_set():
                    break
                self.restart_count += 1
                self.last_error = f'{type(exc).__name__}: {exc}'
                self.logger(
                    f'Критическая ошибка торгового монитора; автоматический перезапуск '
                    f'через 10 сек. restart_count={self.restart_count}: {exc}'
                )
                self._stop_event.wait(10)
        self.logger('Фоновый торговый монитор остановлен.')

    def _process_signals(self, result, chat_id, source):
        new_count = 0
        for signal in result.get('signals', []):
            upsert_watch_candidate(signal, source=source)
            cooldown = number('TRADE_SIGNAL_COOLDOWN_HOURS', 6.0)
            if signal_recently_sent(signal['fingerprint'], cooldown_hours=cooldown):
                continue
            signal_id = save_signal(signal, sent=False)
            cloud_id = persist_trade_signal(signal, source='automatic_monitor' if source == 'monitor' else source)
            if not cloud_id:
                self.logger(f"Monitor signal not confirmed in Supabase: {signal.get('fingerprint')}")
            message = '<b>🚨 НОВЫЙ ТОРГОВЫЙ СИГНАЛ</b>\n\n' + build_signal_block(signal)
            self.sender(chat_id, message)
            try:
                from paper_trading import open_from_signal, format_open_message, format_pending_message, format_missed_message
                paper_result = open_from_signal(signal, source='automatic_monitor' if source == 'monitor' else source)
                if paper_result.get('status') == 'opened':
                    self.sender(chat_id, format_open_message(paper_result))
                elif paper_result.get('status') == 'pending_entry':
                    self.sender(chat_id, format_pending_message(paper_result))
                elif paper_result.get('status') == 'missed_entry':
                    self.sender(chat_id, format_missed_message(paper_result.get('position') or {}, 'MISSED_BREAKOUT'))
            except Exception as exc:
                self.logger(f"Paper trading open error: {exc}")
            mark_signal_sent(signal_id)
            new_count += 1
        return new_count

    def _run_near_signal_cycle(self, chat_id):
        try:
            from core.runtime_state import get as runtime_get
            from strategies.catalog import STRATEGIES
            if (not boolean("STRATEGY_LAB_PARALLEL_WITH_MAIN", True)) and any(runtime_get(f"strategy_{spec.short}").get("running") for spec in STRATEGIES):
                return 0
        except Exception:
            pass
        if not near_signal_config().enabled:
            return 0
        try:
            from near_signal_watchlist import get_due_symbols, mark_checked
            symbols = get_due_symbols()
            if not symbols:
                return 0
            result = run_trade_scan(include_watch=True, max_results=5, source='near-watch', only_symbols=symbols)
            if result.get('busy'):
                return 0
            promoted = [s.get('symbol') for s in result.get('signals', []) if s.get('symbol')]
            retained = result.get('nearWatchSymbols') or []
            mark_checked(symbols, promoted=promoted, retained=retained)
            new_count = self._process_signals(result, chat_id, 'near_watch')
            self.logger(f"Near-signal rescan: symbols={len(symbols)} promoted={len(promoted)} new={new_count}")
            return new_count
        except Exception as exc:
            self.logger(f"Near-signal rescan error: {exc}")
            return 0

    def _loop(self):
        while not self._stop_event.is_set():
            self.heartbeat_at = datetime.now(timezone.utc).isoformat()
            settings = get_monitor_settings()
            if not settings.get('enabled'):
                self._stop_event.wait(5)
                continue

            started = time.time()
            result = {}
            try:
                result = self.run_once(settings)
                self.last_error = None
            except Exception as exc:
                self.last_error = f'{type(exc).__name__}: {exc}'
                self.logger(f'Ошибка торгового монитора: {exc}')

            self.last_run = datetime.now(timezone.utc).isoformat()
            self.heartbeat_at = self.last_run
            base_minutes = max(5, int(settings.get('interval_minutes') or 15))
            active_minutes = integer('TRADE_SCAN_ACTIVE_INTERVAL_MINUTES', 7, minimum=5)
            # More frequent full passes only when there are near-final candidates.
            market_active = int((result.get('universeSummary') or {}).get('activeMarketSymbols') or 0) >= integer('ACTIVE_MARKET_SYMBOL_COUNT', 8, minimum=1)
            interval_minutes = active_minutes if ((result.get('nearMisses') or []) or market_active) else base_minutes
            elapsed = time.time() - started
            wait_seconds = max(30, interval_minutes * 60 - elapsed)
            near_every = max(60, near_signal_config().rescan_minutes * 60)
            shadow_every = max(300, integer('SHADOW_UPDATE_MINUTES', 10, minimum=5) * 60)
            next_near = time.time() + near_every
            next_shadow = time.time() + shadow_every
            deadline = time.time() + wait_seconds
            self.logger(f'Монитор: следующий полный цикл через {int(wait_seconds)} сек.; near-watch каждые {near_every//60} мин.')
            while not self._stop_event.is_set() and time.time() < deadline:
                now_ts = time.time()
                if now_ts >= next_near:
                    self._run_near_signal_cycle(settings.get('chat_id'))
                    next_near = now_ts + near_every
                if now_ts >= next_shadow:
                    try:
                        from shadow_signals import update_shadow_signals
                        update_shadow_signals()
                    except Exception as exc:
                        self.logger(f'Shadow update error: {exc}')
                    next_shadow = now_ts + shadow_every
                self.heartbeat_at = datetime.now(timezone.utc).isoformat()
                self._stop_event.wait(min(30, max(1, deadline - time.time())))

    def _start_parallel_strategy_cycle(self, chat_id=None):
        if not boolean("STRATEGY_LAB_PARALLEL_WITH_MAIN", True):
            return False
        if not boolean("STRATEGY_LAB_SYNC_WITH_MAIN", True):
            return False
        try:
            from strategies.scheduler import run_scheduled_cycle
            def _runner():
                try:
                    result = run_scheduled_cycle(force_parallel_budget=True)
                    self.logger(f"Strategy Lab parallel cycle: status={result.get('status')} runs={len(result.get('runs') or [])}")
                    if chat_id:
                        try:
                            from strategies.notifications import dispatch_pending_notifications
                            dispatch_pending_notifications(self.sender, chat_id, self.logger)
                        except Exception as notify_exc:
                            self.logger(f"Strategy Lab notification error: {notify_exc}")
                except Exception as exc:
                    self.logger(f"Strategy Lab parallel cycle error: {exc}")
            threading.Thread(target=_runner, daemon=True, name="strategy-lab-parallel").start()
            return True
        except Exception as exc:
            self.logger(f"Strategy Lab parallel start error: {exc}")
            return False

    def run_once(self, settings=None):
        if not boolean("STRATEGY_LAB_PARALLEL_WITH_MAIN", True):
            try:
                from core.runtime_state import get as runtime_get
                from strategies.catalog import STRATEGIES
                active = next((spec for spec in STRATEGIES if runtime_get(f"strategy_{spec.short}").get("running")), None)
                if active is not None:
                    self.logger(f"Монитор: полный скан пропущен — выполняется Strategy Lab: {active.title}.")
                    return {"signals": [], "busy": True, "busyOwner": f"strategy_{active.short}"}
            except Exception:
                pass
        settings = settings or get_monitor_settings()
        chat_id = settings.get('chat_id')
        if not chat_id:
            self.logger('Монитор пропустил цикл: chat_id не задан.')
            return {'signals': []}
        self._start_parallel_strategy_cycle(chat_id)
        result = run_trade_scan(include_watch=False, max_results=5, source='monitor')
        if result.get('busy'):
            self.logger('Монитор: полный скан пропущен — scan engine уже занят.')
            return result
        new_count = self._process_signals(result, chat_id, 'monitor')
        self.logger(
            f"Монитор: проверено={result.get('rowsAnalyzed')}, "
            f"сигналов={len(result.get('signals', []))}, новых={new_count}"
        )
        result['newSignalsSent'] = new_count
        return result

