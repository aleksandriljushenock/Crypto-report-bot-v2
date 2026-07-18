import os
import threading
import time
from datetime import datetime, timezone

from trade_engine import run_trade_scan
from trade_signal_report import build_signal_block
from trade_outcome_tracker import register_trade_signal
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

    def _loop(self):
        while not self._stop_event.is_set():
            self.heartbeat_at = datetime.now(timezone.utc).isoformat()
            settings = get_monitor_settings()
            if not settings.get('enabled'):
                self._stop_event.wait(5)
                continue

            started = time.time()
            try:
                self.run_once(settings)
                self.last_error = None
            except Exception as exc:
                self.last_error = f'{type(exc).__name__}: {exc}'
                self.logger(f'Ошибка торгового монитора: {exc}')

            self.last_run = datetime.now(timezone.utc).isoformat()
            self.heartbeat_at = self.last_run
            interval = max(5, int(settings.get('interval_minutes') or 15)) * 60
            elapsed = time.time() - started
            wait_seconds = max(30, interval - elapsed)
            self.logger(f'Монитор: следующий цикл через {int(wait_seconds)} сек.')
            self._stop_event.wait(wait_seconds)

    def run_once(self, settings=None):
        settings = settings or get_monitor_settings()
        chat_id = settings.get('chat_id')
        if not chat_id:
            self.logger('Монитор пропустил цикл: chat_id не задан.')
            return {'signals': []}
        result = run_trade_scan(include_watch=False, max_results=5)
        new_count = 0
        for signal in result.get('signals', []):
            upsert_watch_candidate(signal, source='monitor')
            cooldown = float(os.getenv('TRADE_SIGNAL_COOLDOWN_HOURS', '6'))
            if signal_recently_sent(signal['fingerprint'], cooldown_hours=cooldown):
                continue
            signal_id = save_signal(signal, sent=False)
            register_trade_signal(signal)
            message = '<b>🚨 НОВЫЙ ТОРГОВЫЙ СИГНАЛ</b>\n\n' + build_signal_block(signal)
            self.sender(chat_id, message)
            mark_signal_sent(signal_id)
            new_count += 1
        self.logger(
            f"Монитор: проверено={result.get('rowsAnalyzed')}, "
            f"сигналов={len(result.get('signals', []))}, новых={new_count}"
        )
        result['newSignalsSent'] = new_count
        return result
