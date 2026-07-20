from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone


class RuntimeHealthMonitor:
    def __init__(self, logger, get_trade_monitor, get_automation_supervisor):
        self.logger = logger
        self.get_trade_monitor = get_trade_monitor
        self.get_automation_supervisor = get_automation_supervisor
        self.interval = max(30, int(os.getenv('HEALTH_MONITOR_INTERVAL_SECONDS', '120')))
        self.trade_stale = max(300, int(os.getenv('TRADE_MONITOR_STALE_SECONDS', '1500')))
        self.worker_restart_cooldown = max(30, int(os.getenv('HEALTH_RESTART_COOLDOWN_SECONDS', '120')))
        self._stop = threading.Event()
        self._thread = None
        self._last_restart = {}
        self.last_check = None
        self.last_actions = []

    def start(self):
        if self._thread and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name='runtime-health-monitor', daemon=True)
        self._thread.start()
        self.logger('Health Monitor запущен.')
        return True

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=5)

    def alive(self):
        return bool(self._thread and self._thread.is_alive())

    def snapshot(self):
        
        try:
            from memory_guard import pressure
            memory = pressure()
        except Exception:
            memory = {}
        return {'alive': self.alive(), 'last_check': self.last_check, 'last_actions': list(self.last_actions), 'memory': memory}

    def _can_restart(self, key):
        now = time.time()
        if now - self._last_restart.get(key, 0) < self.worker_restart_cooldown:
            return False
        self._last_restart[key] = now
        return True

    def _loop(self):
        while not self._stop.is_set():
            actions = []
            try:
                from memory_guard import cleanup
                memory = cleanup()
                if memory.get('high'):
                    actions.append(f"memory pressure {memory.get('rssMb')}MB")
                trade = self.get_trade_monitor()
                if trade is not None:
                    stale = False
                    heartbeat = getattr(trade, 'heartbeat_at', None)
                    if heartbeat:
                        try:
                            age = (datetime.now(timezone.utc) - datetime.fromisoformat(heartbeat.replace('Z', '+00:00'))).total_seconds()
                            stale = age > self.trade_stale
                        except Exception:
                            stale = False
                    if (not trade.is_alive() or stale) and self._can_restart('trade-monitor'):
                        trade.stop()
                        time.sleep(1)
                        trade.start()
                        actions.append('trade-monitor restarted' + (' (stale)' if stale else ''))

                supervisor = self.get_automation_supervisor()
                if supervisor is not None:
                    for worker in supervisor.workers:
                        if worker.enabled and not worker.alive() and self._can_restart(worker.name):
                            worker.start()
                            actions.append(f'{worker.name} restarted')
            except Exception as exc:
                self.logger(f'Health Monitor error: {type(exc).__name__}: {exc}')
            self.last_check = datetime.now(timezone.utc).isoformat()
            self.last_actions = actions
            if actions:
                self.logger('Health Monitor: ' + '; '.join(actions))
            self._stop.wait(self.interval)
