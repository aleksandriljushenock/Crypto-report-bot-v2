from __future__ import annotations
import html
from datetime import datetime


def render_diagnostics(data: dict) -> str:
    runtime = data.get("runtime") or {}
    scanner = runtime.get("scanner") or {}
    heavy = runtime.get("heavy_task") or {}
    providers = data.get("providers") or []
    problems = data.get("provider_problems") or []
    events = data.get("events") or []
    online = sum(1 for p in providers if p.get("status") == "online")
    lines = [
        "🛠 <b>ДИАГНОСТИКА</b>", "",
        f"Scanner: <b>{'BUSY' if scanner.get('running') else 'READY'}</b>",
        f"Heavy task: <b>{html.escape(str(heavy.get('name') or 'none')) if heavy.get('running') else 'none'}</b>",
        f"Биржи online: <b>{online}/{len(providers)}</b>",
    ]
    if problems:
        lines += ["", "⚠️ <b>Проблемные источники</b>"]
        for row in problems[:6]:
            lines.append(f"• {html.escape(str(row.get('provider')))} — {html.escape(str(row.get('status')))}")
    if events:
        lines += ["", "🧾 <b>Последние события</b>"]
        for row in events[-8:]:
            at = str(row.get("at") or "")
            try:
                at = datetime.fromisoformat(at.replace("Z", "+00:00")).astimezone().strftime("%H:%M:%S")
            except Exception:
                at = at[-8:] if at else "--:--:--"
            lines.append(f"• <code>{at}</code> {html.escape(str(row.get('event') or 'EVENT'))}")
    lines += ["", "Диагностика читает тот же runtime-state, что сканер и Status. Отдельных противоречивых lock-статусов больше нет."]
    return "\n".join(lines)
