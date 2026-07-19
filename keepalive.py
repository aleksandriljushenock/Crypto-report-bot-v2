from __future__ import annotations

import os
import threading
import time
import urllib.request
from datetime import datetime, timezone


class KeepAliveService:
    """Periodically calls the public /wake endpoint to prevent idle spin-down.

    This is a best-effort helper for free hosting. A paid always-on instance or an
    external uptime monitor remains the reliable option because this thread stops
    whenever the hosting platform suspends the whole process.
    """

    def __init__(self, logger):
        self.logger = logger
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_ping_at: str | None = None
        self.last_success_at: str | None = None
        self.last_error: str | None = None
        self.success_count = 0
        self.failure_count = 0

    @staticmethod
    def _enabled() -> bool:
        return os.getenv("KEEPALIVE_ENABLED", "true").strip().lower() in {
            "1", "true", "yes", "on"
        }

    @staticmethod
    def _interval_seconds() -> int:
        try:
            return max(60, int(os.getenv("KEEPALIVE_INTERVAL_SECONDS", "600")))
        except ValueError:
            return 600

    @staticmethod
    def _initial_delay_seconds() -> int:
        try:
            return max(5, int(os.getenv("KEEPALIVE_INITIAL_DELAY_SECONDS", "60")))
        except ValueError:
            return 60

    @staticmethod
    def _target_url() -> str:
        explicit = os.getenv("KEEPALIVE_URL", "").strip()
        if explicit:
            return explicit
        base = (
            os.getenv("PUBLIC_BASE_URL", "").strip()
            or os.getenv("RENDER_EXTERNAL_URL", "").strip()
        ).rstrip("/")
        return f"{base}/wake" if base else ""

    def start(self) -> bool:
        if not self._enabled():
            self.logger("KeepAlive отключен через KEEPALIVE_ENABLED.")
            return False
        if self._thread and self._thread.is_alive():
            return False
        target = self._target_url()
        if not target:
            self.logger("KeepAlive не запущен: отсутствует PUBLIC_BASE_URL/RENDER_EXTERNAL_URL.")
            return False
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="render-keepalive",
            daemon=True,
        )
        self._thread.start()
        self.logger(
            f"KeepAlive запущен: interval={self._interval_seconds()}s target={target}"
        )
        return True

    def stop(self, join_timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=join_timeout)

    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def snapshot(self) -> dict:
        return {
            "enabled": self._enabled(),
            "alive": self.alive(),
            "target": self._target_url(),
            "interval_seconds": self._interval_seconds(),
            "last_ping_at": self.last_ping_at,
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
        }

    def _run(self) -> None:
        if self._stop.wait(self._initial_delay_seconds()):
            return
        while not self._stop.is_set():
            self._ping_once()
            if self._stop.wait(self._interval_seconds()):
                return

    def _ping_once(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.last_ping_at = now
        try:
            req = urllib.request.Request(
                self._target_url(),
                headers={"User-Agent": "crypto-report-bot-keepalive/1.0"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=20) as response:
                status = int(getattr(response, "status", 200))
                if status >= 400:
                    raise RuntimeError(f"HTTP {status}")
            self.last_success_at = datetime.now(timezone.utc).isoformat()
            self.last_error = None
            self.success_count += 1
            self.logger(f"KeepAlive ping: success status={status}")
        except Exception as exc:
            self.failure_count += 1
            self.last_error = str(exc)
            self.logger(f"KeepAlive ping: error={exc}")
