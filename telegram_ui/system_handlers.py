"""System-menu callback handlers.

This module intentionally contains no scanner/trading calculations; it composes
application services and renderers only.
"""
from __future__ import annotations
from typing import Any

from application.diagnostics_service import snapshot_report
from application.system_service import runtime_context
from telegram_ui.client import send_message
from telegram_ui.diagnostics import render_diagnostics
from telegram_ui.keyboards import dashboard_keyboard, system_keyboard
from telegram_ui.status_view import render_dashboard


def handle(callback_data: str, chat_id: Any, *, flags: dict, chronos_text: str) -> bool:
    if callback_data == "dashboard":
        send_message(chat_id, render_dashboard(runtime_context(**flags), chronos_text), reply_markup=dashboard_keyboard())
        return True
    if callback_data == "health_check":
        try:
            from healthcheck import main as run_healthcheck
            result = run_healthcheck()
            text = "✅ <b>Health Check пройден</b>" if result == 0 else "❌ <b>Health Check не пройден</b>"
        except Exception as exc:
            text = f"❌ <b>Health Check error</b>\n<code>{str(exc)[:500]}</code>"
        send_message(chat_id, text, reply_markup=system_keyboard())
        return True
    if callback_data == "server_status":
        from server_status import build_server_status
        send_message(chat_id, build_server_status(), reply_markup=system_keyboard())
        return True
    if callback_data == "bot_status":
        send_message(chat_id, render_diagnostics(snapshot_report()), reply_markup=system_keyboard())
        return True
    return False
