from __future__ import annotations

import random
import threading
import time
from typing import Callable, Any

class PeriodicWorker:
    def __init__(self, name: str, interval_seconds: int, target: Callable[[], Any], logger: Callable[[str], None], enabled: bool = True, first_delay: int = 10, jitter_seconds: int = 5):
        self.name = name
        self.interval_seconds = max(60, int(interval_seconds))
        self.target = target
        self.logger = logger
        self.enabled = enabled
        self.first_delay = max(0, int(first_delay))
        self.jitter_seconds = max(0, int(jitter_seconds))
        self._thread = None
        self._stop = threading.Event()

    def start(self) -> bool:
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name=self.name)
        self._thread.start()
        return True

    def stop(self, join_timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=join_timeout)

    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        if self._stop.wait(self.first_delay):
            return
        while not self._stop.is_set():
            started = time.monotonic()
            result = None
            try:
                result = self.target()
            except Exception as exc:
                self.logger(f"{self.name}: {exc}")
            elapsed = time.monotonic() - started
            delay = max(30.0, self.interval_seconds - elapsed)
            # A transient memory/busy guard must not postpone a critical job for
            # its full multi-hour interval. Retry quickly and autonomously.
            if isinstance(result, dict) and result.get('status') in {'skipped-memory','skipped-busy'}:
                delay = min(delay, 60.0)
            if self.jitter_seconds:
                delay += random.uniform(0, self.jitter_seconds)
            self._stop.wait(delay)
