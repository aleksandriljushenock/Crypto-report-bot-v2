from __future__ import annotations
import html
from strategies.service import latest_run, stats


def _p(v):
    try:
        v = float(v)
    except Exception:
        return "—"
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    if abs(v) >= 1:
        return f"{v:.4f}".rstrip("0").rstrip(".")
    return f"{v:.8f}".rstrip("0").rstrip(".")


def strategy_home_text():
    return (
        "🧭 <b>СТРАТЕГИИ</b>\n"
        "Отдельные торговые гипотезы с собственным анализом и статистикой.\n\n"
        "Сейчас доступна первая стратегия:\n"
        "<b>Fib 0.5 Pullback</b> — D1 тренд + откат к 0.5 + support + H4 подтверждение."
    )


def fib_home_text():
    return (
        "🟦 <b>FIB 0.5 PULLBACK</b>\n\n"
        "Ликвидность ≥ <b>$100M/24h</b> → D1 swing/trend → Fib 0.5 → "
        "реальная support-zone → H4 trigger.\n\n"
        "TP: около D1 swing high.\nSL: ниже support и Fib 0.5.\n\n"
        "Стратегия ведёт отдельную статистику и пока не смешивается с основными сигналами/Paper Trading."
    )


def scan_report(result):
    s = result.get("summary", {})
    lines = [
        "🟦 <b>FIB 0.5 — РЕЗУЛЬТАТ СКАНА</b>",
        f"Ликвидных ≥ $100M: <b>{s.get('eligible_total', 0)}</b>",
        f"Проанализировано: <b>{s.get('analyzed', 0)}</b>",
        f"🔥 Ready: <b>{s.get('ready', 0)}</b> · 🟡 Watch: <b>{s.get('watch', 0)}</b> · ⏳ Waiting: <b>{s.get('waiting', 0)}</b>",
    ]
    if s.get("errors"):
        lines.append(f"⚠️ Ошибок API: {s.get('errors')}")
    shown = [x for x in result.get("results", []) if x.get("status") in {"READY", "WATCH", "WAITING"}][:10]
    if shown:
        lines += ["", "<b>Лучшие сетапы:</b>"]
    for x in shown:
        icon = {"READY":"🔥", "WATCH":"🟡", "WAITING":"⏳"}.get(x.get("status"), "•")
        lines += [
            f"\n{icon} <b>{html.escape(x['symbol'])}</b> · {x.get('status')} · Score {x.get('score', 0):.0f}",
            f"D1: {_p(x.get('d1_low'))} → {_p(x.get('d1_high'))} · Fib 0.5 {_p(x.get('fib_05'))}",
            f"Support: {_p(x.get('support_low'))}–{_p(x.get('support_high'))} · touches {x.get('support_touches', 0)}",
            f"Entry {_p(x.get('entry_price'))} · SL {_p(x.get('stop_price'))} · TP {_p(x.get('tp_price'))} · R/R {x.get('rr', 0):.2f}",
            f"H4: {html.escape(str(x.get('h4_reason') or '—'))}",
        ]
    if not shown:
        lines += ["", "Подходящих D1/H4 сетапов сейчас нет."]
    return "\n".join(lines)


def candidates_text():
    run = latest_run()
    if not run:
        return "🟡 <b>КАНДИДАТЫ</b>\n\nСначала запусти анализ стратегии."
    rows = run.get("candidates") or []
    rows = [x for x in rows if x.get("status") in {"READY", "WATCH", "WAITING"}][:15]
    if not rows:
        return "🟡 <b>КАНДИДАТЫ</b>\n\nВ последнем скане подходящих кандидатов нет."
    lines = ["🟡 <b>КАНДИДАТЫ FIB 0.5</b>"]
    for x in rows:
        lines.append(
            f"\n<b>{html.escape(x.get('symbol',''))}</b> · {x.get('status')} · Score {float(x.get('score') or 0):.0f}\n"
            f"Entry {_p(x.get('entry_price'))} · RR {float(x.get('rr') or 0):.2f} · до зоны {float(x.get('distance_to_zone_pct') or 0):.1f}%"
        )
    return "\n".join(lines)


def winrate_text():
    s = stats()
    lines = [
        "📈 <b>FIB 0.5 — WIN RATE</b>",
        f"Всего зафиксировано: <b>{s['total']}</b>",
        f"Завершено: <b>{s['resolved']}</b>",
        f"Побед / убытков: <b>{s['wins']} / {s['losses']}</b>",
        f"Win Rate: <b>{s['win_rate']:.1f}%</b>",
        f"Profit Factor: <b>{s['profit_factor']:.2f}</b>",
        f"Средний return: <b>{s['avg_return']:+.2f}%</b>",
        "",
        f"Ожидают entry: {s['waiting']} · Открыты: {s['open']} · Expired: {s['expired']}",
    ]
    if s["resolved"] < 20:
        lines += ["", "⚠️ Выборка пока маленькая. Для оценки стратегии желательно минимум 30–50 завершённых сетапов."]
    return "\n".join(lines)


def history_text():
    s = stats()
    rows = s.get("recent") or []
    if not rows:
        return "📜 <b>ИСТОРИЯ FIB 0.5</b>\n\nИстория пока пуста."
    lines = ["📜 <b>ИСТОРИЯ FIB 0.5</b>"]
    for x in rows[:12]:
        state = x.get("state") or "—"
        ret = x.get("return_pct")
        suffix = f" · {float(ret):+.2f}%" if ret is not None else ""
        lines.append(f"\n<b>{html.escape(x.get('symbol',''))}</b> · {state}{suffix}\nEntry {_p(x.get('entry_price'))} · RR {float(x.get('rr') or 0):.2f}")
    return "\n".join(lines)


def rules_text():
    return (
        "📐 <b>ПРАВИЛА FIB 0.5 PULLBACK</b>\n\n"
        "1. USDT perpetual с 24h quote volume ≥ $100M.\n"
        "2. На D1 ищется последний значимый восходящий swing.\n"
        "3. Предпочтение HH + HL; fallback — цена выше D1 SMA20 и swing ≥ 8%.\n"
        "4. Fib 0.5 строится между swing low и swing high.\n"
        "5. Нужна самостоятельная support-zone около 0.5, а не только уровень Fibonacci.\n"
        "6. На H4 цена должна прийти к зоне и дать bullish подтверждение: BOS / engulfing / higher-low.\n"
        "7. Entry — confluence support/Fib. SL — ниже support с ATR buffer.\n"
        "8. TP — перед D1 swing high. Минимально приемлемый R/R — 2.0.\n"
        "9. Для статистики касание Entry и SL в одной H4 свече считается консервативно как SL.\n\n"
        "Это отдельная исследовательская стратегия; она не открывает основной Paper автоматически."
    )
