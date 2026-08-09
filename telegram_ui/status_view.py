from __future__ import annotations
import html
from datetime import datetime

_OWNER = {
    "automatic_monitor": "фоновый монитор",
    "monitor": "фоновый монитор",
    "manual_trade_scan": "ручной скан",
    "manual": "ручной скан",
    "near_signal_watch": "Near-Signal re-scan",
    "near_watch": "Near-Signal re-scan",
    "shadow": "Shadow update",
}
_PHASE = {
    "idle": "ожидание",
    "universe": "🌍 сбор Universe",
    "market_data": "⚡ Fast/market data",
    "analysis": "🔬 Deep Scan",
    "ranking": "🧠 ranking / AI",
    "hedge": "🧠 Hedge / AI",
    "finalizing": "✅ финализация результатов",
    "chronos": "🧠 Chronos",
    "near_signal": "🟡 Near Signals",
    "shadow": "👻 Shadow update",
}


def _elapsed(started_at):
    if not started_at:
        return None
    try:
        start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        now = datetime.now(start.tzinfo) if start.tzinfo else datetime.now()
        seconds = max(0, int((now - start).total_seconds()))
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours}ч {minutes:02d}м {seconds:02d}с" if hours else f"{minutes:02d}м {seconds:02d}с"
    except Exception:
        return None


def render_dashboard(ctx: dict, chronos_text: str) -> str:
    flags = ctx.get("flags") or {}
    monitor_enabled = bool(flags.get("monitor_enabled"))
    monitor_alive = bool(flags.get("monitor_alive"))
    manual_alive = bool(flags.get("manual_thread_alive"))
    scanner = ctx.get("scanner") or {}
    engine_busy = bool(scanner.get("running"))
    lines = [
        "📟 <b>ПАНЕЛЬ СОСТОЯНИЯ</b>", "",
        f"📡 Монитор: <b>{'🟢 работает' if monitor_enabled and monitor_alive else ('🟡 включён, процесс не активен' if monitor_enabled else '⚪ остановлен')}</b>",
    ]
    if engine_busy:
        owner_raw = str(scanner.get("owner") or "").strip().lower()
        owner = _OWNER.get(owner_raw, owner_raw.replace("_", " ") if owner_raw else "неизвестно")
        phase_raw = str(scanner.get("phase") or "idle").strip().lower()
        phase = _PHASE.get(phase_raw, phase_raw.replace("_", " "))
        processed = int(scanner.get("processed") or 0)
        total = int(scanner.get("total") or 0)
        lines += ["", "🔍 Сканер: <b>🟡 выполняется</b>", f"├ Источник: <b>{html.escape(owner)}</b>", f"├ Этап: <b>{html.escape(phase)}</b>"]
        if total > 0:
            lines.append(f"├ Прогресс: <b>{processed}/{total}</b> ({min(100, max(0, int(processed*100/total)))}%)")
        else:
            lines.append("├ Прогресс: <b>подготовка...</b>")
        elapsed = _elapsed(scanner.get("startedAt"))
        if elapsed:
            lines.append(f"└ В работе: <b>{elapsed}</b>")
    else:
        lines += ["", "🔍 Сканер: <b>🟢 готов</b>", "└ Активного ручного или фонового прохода нет"]
        last = ctx.get("last_scan") or {}
        stages = last.get("stages") or {}
        last_at = last.get("runTimeUtc") or last.get("savedAt")
        if last_at:
            try:
                dt = datetime.fromisoformat(str(last_at).replace("Z", "+00:00"))
                lines.append(f"   Последний проход: <b>{dt.astimezone().strftime('%H:%M:%S')}</b>")
            except Exception:
                pass
        if stages:
            lines.append(f"   Проверено: <b>{int(stages.get('analyzed') or 0)}</b> · сигналов: <b>{int(stages.get('signals') or 0)}</b>")

    lines += ["", f"⚡ Ручной запуск: <b>{'занят общим сканером' if engine_busy else ('выполняется' if manual_alive else 'доступен')}</b>", f"🧠 Chronos: <b>{chronos_text}</b>"]
    heavy = ctx.get("heavy_task") or {}
    if heavy.get("running"):
        name = str(heavy.get("name") or "background task").replace("-", " ")
        lines.append(f"⚙️ Фоновая задача: <b>🟡 {html.escape(name)}</b>")
    else:
        lines.append("⚙️ Фоновая задача: <b>🟢 нет тяжёлых задач</b>")
    extras = []
    if flags.get("report_alive"): extras.append("market report")
    if flags.get("listing_alive"): extras.append("база листингов")
    if extras:
        lines.append(f"⚙️ Другие задачи: <b>{html.escape(', '.join(extras))}</b>")

    cfg = ctx.get("scanner_config") or {}
    lines += ["", "🔎 <b>Параметры сканера</b>", f"Fast pool: <b>{cfg.get('fast_pool')}</b> · Deep: <b>{cfg.get('deep_limit')}</b>", f"Batch: <b>{cfg.get('batch_size')}</b> · Workers: <b>{cfg.get('workers')}</b> · Hedge: <b>{cfg.get('hedge_pool')}</b>"]
    mem = ctx.get("memory") or {}
    if mem.get("rss_mb") is not None or mem.get("peak_mb") is not None:
        current = f"{mem['rss_mb']:.0f} MB" if mem.get("rss_mb") is not None else "N/A"
        peak = f"{mem['peak_mb']:.0f} MB" if mem.get("peak_mb") is not None else "N/A"
        lines += ["", "🧠 <b>Процесс бота</b>", f"RAM: <b>{current}</b> · Peak: <b>{peak}</b>"]
    try:
        updated = datetime.fromisoformat(str(ctx.get("updated_at"))).strftime("%H:%M:%S")
    except Exception:
        updated = datetime.now().strftime("%H:%M:%S")
    lines += ["", f"🕒 Обновлено: <b>{updated}</b>", "", "Нажми «🔄 Обновить панель» для актуального состояния."]
    return "\n".join(lines)
