from __future__ import annotations

import atexit
import hmac
import os
import threading
import time
from datetime import datetime, timezone
from typing import Callable

from flask import Flask, jsonify, request

from keepalive import KeepAliveService

from telegram_command_bot import (
    get_webhook_info,
    log,
    process_update,
    set_webhook,
    start_runtime_services,
    stop_runtime_services,
)

app = Flask(__name__)

_started_at = datetime.now(timezone.utc)
_runtime_lock = threading.Lock()
_runtime_started = False
_task_locks: dict[str, threading.Lock] = {}
_keepalive = KeepAliveService(log)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _constant_time_equal(left: str, right: str) -> bool:
    return bool(left and right) and hmac.compare_digest(left, right)


def _public_base_url() -> str:
    return (_env("PUBLIC_BASE_URL") or _env("RENDER_EXTERNAL_URL")).rstrip("/")


def _webhook_secret() -> str:
    return _env("TELEGRAM_WEBHOOK_SECRET")


def _ensure_runtime() -> None:
    global _runtime_started
    if _runtime_started:
        return
    with _runtime_lock:
        if _runtime_started:
            return

        secret = _webhook_secret()
        if len(secret) < 24:
            raise RuntimeError("TELEGRAM_WEBHOOK_SECRET must be configured with at least 24 characters")
        start_runtime_services()

        base_url = _public_base_url()
        if not base_url:
            raise RuntimeError("Не задан PUBLIC_BASE_URL или RENDER_EXTERNAL_URL")

        set_webhook(
            f"{base_url}/telegram",
            secret_token=secret,
            drop_pending_updates=_env("DROP_PENDING_UPDATES", "false").lower() in {"1", "true", "yes", "on"},
        )
        _runtime_started = True
        _keepalive.start()
        log("Render webhook runtime полностью инициализирован.")


def _shutdown_runtime() -> None:
    global _runtime_started
    if not _runtime_started:
        return
    try:
        _keepalive.stop()
        stop_runtime_services()
    finally:
        _runtime_started = False


def _authorized_cron_request() -> bool:
    expected = _env("CRON_SECRET")
    header = request.headers.get("Authorization", "")
    supplied = header[7:].strip() if header.startswith("Bearer ") else ""
    return _constant_time_equal(supplied, expected)


def _task_registry() -> dict[str, Callable[[], object]]:
    from telegram_command_bot import automation_supervisor

    if automation_supervisor is None:
        return {}
    return {
        "discovery": automation_supervisor._guarded("render-discovery", automation_supervisor._run_discovery),
        "listings": automation_supervisor._guarded("render-listings", automation_supervisor._run_listing_refresh),
        "trade-outcomes": automation_supervisor._guarded("render-trade-outcomes", automation_supervisor._run_trade_outcomes),
        "outcomes": automation_supervisor._guarded("render-outcomes", automation_supervisor._run_outcomes),
        "capital-flows": automation_supervisor._guarded("render-capital-flows", automation_supervisor._run_capital_flows),
        "news": automation_supervisor._guarded("render-news", automation_supervisor._run_news),
        "narratives": automation_supervisor._guarded("render-narratives", automation_supervisor._run_narratives),
        "smart-money": automation_supervisor._guarded("render-smart-money", automation_supervisor._run_smart_money),
        "ai": automation_supervisor._guarded("render-ai", automation_supervisor._run_ai_intelligence),
        "learning": automation_supervisor._guarded("render-learning", automation_supervisor._run_learning),
    }


def _run_task_in_background(name: str, callback: Callable[[], object]) -> bool:
    lock = _task_locks.setdefault(name, threading.Lock())
    if not lock.acquire(blocking=False):
        return False

    def runner() -> None:
        try:
            result = callback()
            log(f"Render task {name}: success result={result}")
        except Exception as exc:
            log(f"Render task {name}: error={exc}")
        finally:
            lock.release()

    threading.Thread(target=runner, name=f"render-task-{name}", daemon=True).start()
    return True


@app.get("/")
def index():
    return jsonify(
        service="crypto-report-bot",
        status="ok",
        mode="telegram-webhook",
        started_at=_started_at.isoformat(),
    )


@app.get("/health")
def health():
    try:
        _ensure_runtime()
        info = get_webhook_info()
        from telegram_command_bot import trade_monitor, automation_supervisor, runtime_health_monitor
        trade_status = {
            "alive": bool(trade_monitor and trade_monitor.is_alive()),
            "last_run": getattr(trade_monitor, "last_run", None),
            "last_error": getattr(trade_monitor, "last_error", None),
            "restart_count": getattr(trade_monitor, "restart_count", 0),
            "heartbeat_at": getattr(trade_monitor, "heartbeat_at", None),
        }
        automation_status = automation_supervisor.status() if automation_supervisor else {}
        health_status = runtime_health_monitor.snapshot() if runtime_health_monitor else {"alive": False}
        degraded = not trade_status["alive"] or not health_status.get("alive", False)
        return jsonify(
            status="degraded" if degraded else "healthy",
            runtime_started=_runtime_started,
            webhook_url=(info or {}).get("url"),
            pending_updates=(info or {}).get("pending_update_count", 0),
            trade_monitor=trade_status,
            automation=automation_status,
            health_monitor=health_status,
            keepalive=_keepalive.snapshot(),
            uptime_seconds=int((datetime.now(timezone.utc) - _started_at).total_seconds()),
        ), (503 if degraded else 200)
    except Exception as exc:
        log(f"Health check failed: {exc}")
        return jsonify(status="unhealthy", error=str(exc)), 503


@app.post("/telegram")
def telegram_webhook():
    _ensure_runtime()

    expected = _webhook_secret()
    if len(expected) < 24:
        return jsonify(ok=False, error="webhook secret not configured"), 503
    supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not _constant_time_equal(supplied, expected):
        return jsonify(ok=False, error="unauthorized"), 401

    update = request.get_json(silent=True)
    if not isinstance(update, dict) or "update_id" not in update:
        return jsonify(ok=False, error="invalid update"), 400

    # Telegram expects a quick 2xx response. Existing command handlers already
    # move long-running analyses to their own threads, so processing here is safe.
    process_update(update)
    return jsonify(ok=True)


@app.post("/tasks/<name>")
def run_task(name: str):
    _ensure_runtime()
    if not _authorized_cron_request():
        return jsonify(ok=False, error="unauthorized"), 401

    callback = _task_registry().get(name)
    if callback is None:
        return jsonify(ok=False, error="unknown task", available=sorted(_task_registry())), 404

    started = _run_task_in_background(name, callback)
    if not started:
        return jsonify(ok=False, status="already-running", task=name), 409
    return jsonify(ok=True, status="started", task=name), 202


@app.post("/admin/register-webhook")
def register_webhook():
    if not _authorized_cron_request():
        return jsonify(ok=False, error="unauthorized"), 401
    _ensure_runtime()
    base_url = _public_base_url()
    set_webhook(f"{base_url}/telegram", secret_token=_webhook_secret())
    return jsonify(ok=True, webhook=f"{base_url}/telegram")


@app.get("/wake")
def wake():
    # Lightweight endpoint for internal/external uptime monitors.
    _ensure_runtime()
    return jsonify(
        ok=True,
        runtime_started=_runtime_started,
        keepalive_alive=_keepalive.alive(),
        timestamp=int(time.time()),
    )


atexit.register(_shutdown_runtime)

# Gunicorn imports this module once because render.yaml fixes workers to 1.
_ensure_runtime()
