from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from core.runtime_config import boolean, integer
from strategies.catalog import get_strategy
from strategies.repository import repository

logger = logging.getLogger("strategy_notifications")


def _p(value: Any) -> str:
    try:
        value = float(value)
    except Exception:
        return "—"
    if abs(value) >= 1000:
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    if abs(value) >= 1:
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return f"{value:.10f}".rstrip("0").rstrip(".")


def _reason(row: dict[str, Any]) -> str:
    payload = row.get("payload") or {}
    return str(payload.get("reason") or payload.get("main_reason") or payload.get("why_enter") or "Сетап прошёл правила стратегии.")


def _entry_mode(row: dict[str, Any]) -> str:
    return str((row.get("payload") or {}).get("entry_mode") or "LIMIT").upper()


def render_notification(event_type: str, row: dict[str, Any]) -> str:
    spec = get_strategy(str(row.get("strategy") or "fib_05_pullback"))
    symbol = html.escape(str(row.get("symbol") or ""))
    direction = html.escape(str(row.get("direction") or "LONG").upper())
    event_type = str(event_type or "").upper()

    if event_type == "READY":
        mode = _entry_mode(row)
        wait_text = "ждём касания Entry" if mode == "LIMIT" else "ждём пробоя Entry"
        return (
            "🧭 <b>STRATEGY SIGNAL</b>\n\n"
            f"{spec.emoji} <b>{html.escape(spec.title)}</b>\n"
            f"{'🟢' if direction == 'LONG' else '🔴'} <b>{direction} {symbol}</b>\n\n"
            f"Entry: <b>{_p(row.get('entry_price'))}</b>\n"
            f"SL: <b>{_p(row.get('stop_price'))}</b>\n"
            f"TP: <b>{_p(row.get('tp_price'))}</b>\n"
            f"R/R: <b>{float(row.get('rr') or 0):.2f}</b> · Score: <b>{float(row.get('score') or 0):.0f}</b>\n\n"
            f"Статус: ⏳ <b>{wait_text}</b>\n"
            f"<i>{html.escape(_reason(row))}</i>"
        )

    if event_type == "OPEN":
        entered_at = row.get("entered_at") or "—"
        return (
            "✅ <b>STRATEGY ENTRY FILLED</b>\n\n"
            f"{spec.emoji} <b>{html.escape(spec.title)}</b>\n"
            f"{'🟢' if direction == 'LONG' else '🔴'} <b>{direction} {symbol}</b>\n\n"
            f"Entry: <b>{_p(row.get('entry_price'))}</b>\n"
            f"SL: <b>{_p(row.get('stop_price'))}</b> · TP: <b>{_p(row.get('tp_price'))}</b>\n"
            f"R/R: <b>{float(row.get('rr') or 0):.2f}</b>\n"
            f"Время: <code>{html.escape(str(entered_at))}</code>"
        )

    outcome = str(row.get("outcome") or "CLOSED")
    ret = float(row.get("return_pct") or 0)
    if outcome == "MA55_REVERSE_CROSS":
        close_reason = "SMA55 пересекла EMA8/SMA13/SMA21 снизу вверх — штатный выход."
    elif outcome == "RISK_SL":
        close_reason = "Сработал защитный risk-stop."
    elif outcome.startswith("TP") or outcome == "TP":
        close_reason = "Достигнута целевая зона стратегии."
    elif "SL" in outcome:
        close_reason = "Сработал защитный stop-loss."
    else:
        close_reason = outcome
    icon = "🏁" if ret >= 0 else "🛑"
    return (
        f"{icon} <b>STRATEGY CLOSED</b>\n\n"
        f"{spec.emoji} <b>{html.escape(spec.title)}</b>\n"
        f"<b>{direction} {symbol}</b>\n\n"
        f"Entry: <b>{_p(row.get('entry_price'))}</b>\n"
        f"Result: <b>{ret:+.2f}%</b>\n"
        f"Outcome: <b>{html.escape(outcome)}</b>\n\n"
        f"{html.escape(close_reason)}"
    )


def dispatch_pending_notifications(
    sender: Callable[[Any, str], Any],
    chat_id: Any,
    log: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Send durable Strategy Lab lifecycle notifications.

    Rows are marked as notified only after Telegram send succeeds, so a transient
    Telegram error is retried on the next cycle instead of silently losing the alert.
    """
    if not chat_id or not boolean("STRATEGY_LAB_NOTIFY_ENABLED", True):
        return {"status": "disabled", "sent": 0, "errors": 0}

    max_age = integer("STRATEGY_LAB_NOTIFY_MAX_AGE_HOURS", 24, minimum=1, maximum=168)
    limit = integer("STRATEGY_LAB_NOTIFY_MAX_PER_CYCLE", 30, minimum=1, maximum=200)
    try:
        pending = repository.pending_notifications(max_age_hours=max_age, limit=limit)
    except Exception as exc:
        if log:
            log(f"Strategy notifications unavailable (run v33 migration): {exc}")
        return {"status": "repository-error", "sent": 0, "errors": 1, "error": str(exc)}

    sent = 0
    errors = 0
    for item in pending:
        event_type = str(item.get("event_type") or "").upper()
        row = item.get("setup") or {}
        if event_type == "READY" and not boolean("STRATEGY_LAB_NOTIFY_READY", True):
            continue
        if event_type == "OPEN" and not boolean("STRATEGY_LAB_NOTIFY_FILLED", True):
            continue
        if event_type == "CLOSED" and not boolean("STRATEGY_LAB_NOTIFY_CLOSED", True):
            continue
        if str(row.get("strategy") or "") == "ma55_cycle" and not boolean("STRATEGY_MA55CYCLE_NOTIFY", True):
            continue
        try:
            sender(chat_id, render_notification(event_type, row))
            repository.mark_notification_sent(row.get("id"), event_type)
            sent += 1
        except Exception as exc:
            errors += 1
            if log:
                log(f"Strategy notification send failed {row.get('strategy')}/{row.get('symbol')}/{event_type}: {exc}")
    return {"status": "ok", "sent": sent, "errors": errors, "pending": len(pending)}
