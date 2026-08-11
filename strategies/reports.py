from __future__ import annotations

import html

from strategies.catalog import STRATEGIES, get_strategy
from strategies.service import latest_run, stats, leaderboard
from strategies.scheduler import status as scheduler_status


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
    auto = scheduler_status()
    state = "🟢 ON" if auto.get("enabled") else "⚪ OFF"
    mode = "по одной стратегии (round-robin)" if auto.get("mode") == "round_robin" else "все стратегии за цикл"
    return (
        "🧭 <b>STRATEGY LAB</b>\n"
        "Независимые торговые гипотезы с отдельным forward-tracking и статистикой.\n\n"
        f"Доступно стратегий: <b>{len(STRATEGIES)}</b>.\n"
        f"⏱ Автоанализ: <b>{state}</b> · каждые <b>{auto.get('interval_minutes')} мин</b> · {mode}.\n"
        "Автоскан не запускается параллельно с основным Deep Scan и тяжёлыми фоновыми задачами.\n\n"
        "Каждая стратегия анализируется отдельно и пока не смешивается с основным Paper Trading.\n"
        "🏆 Leaderboard сравнивает Win Rate, Profit Factor и expectancy только по реально завершённым forward-setups."
    )


def strategy_detail_text(strategy: str):
    spec = get_strategy(strategy)
    return (
        f"{spec.emoji} <b>{html.escape(spec.title.upper())}</b>\n\n"
        f"{html.escape(spec.description)}\n\n"
        "Стратегия ведёт отдельные кандидаты, outcomes и статистику. "
        "READY-setups сохраняются только для будущего наблюдения — исторический вход задним числом не создаётся."
    )


def scan_report(result, strategy: str | None = None):
    strategy = strategy or result.get("strategy") or result.get("summary", {}).get("strategy") or "fib_05_pullback"
    spec = get_strategy(strategy)
    s = result.get("summary", {})
    lines = [
        f"{spec.emoji} <b>{html.escape(spec.title.upper())} — СКАН</b>",
        f"Ликвидных в universe: <b>{s.get('eligible_total', 0)}</b>",
        f"Проанализировано: <b>{s.get('analyzed', 0)}</b>",
        f"🔥 Ready: <b>{s.get('ready', 0)}</b> · 🟡 Watch: <b>{s.get('watch', 0)}</b> · ⏳ Waiting: <b>{s.get('waiting', 0)}</b>",
    ]
    if s.get("errors"):
        lines.append(f"⚠️ Ошибок API: {s.get('errors')}")
    shown = [x for x in result.get("results", []) if x.get("status") in {"READY", "WATCH", "WAITING"}][:10]
    if shown:
        lines += ["", "<b>Лучшие сетапы:</b>"]
    for x in shown:
        icon = {"READY": "🔥", "WATCH": "🟡", "WAITING": "⏳"}.get(x.get("status"), "•")
        direction = x.get("direction") or "—"
        lines += [
            f"\n{icon} <b>{html.escape(x.get('symbol',''))}</b> · {direction} · {x.get('status')} · Score {float(x.get('score') or 0):.0f}",
            f"Entry {_p(x.get('entry_price'))} · SL {_p(x.get('stop_price'))} · TP {_p(x.get('tp_price'))} · R/R {float(x.get('rr') or 0):.2f}",
            f"{html.escape(str(x.get('reason') or '—'))}",
        ]
        if x.get("funding_rate") is not None or x.get("oi_change_pct") is not None:
            lines.append(f"Funding: {_p(x.get('funding_rate'))} · OI Δ: {_p(x.get('oi_change_pct'))}%")
    if not shown:
        lines += ["", "Подходящих сетапов сейчас нет."]
    return "\n".join(lines)


def candidates_text(strategy: str = "fib_05_pullback"):
    spec = get_strategy(strategy)
    run = latest_run(spec.key)
    if not run:
        return f"🟡 <b>{html.escape(spec.title)} — КАНДИДАТЫ</b>\n\nСначала запусти анализ стратегии."
    rows = run.get("candidates") or []
    rows = [x for x in rows if x.get("status") in {"READY", "WATCH", "WAITING"}][:15]
    if not rows:
        return f"🟡 <b>{html.escape(spec.title)} — КАНДИДАТЫ</b>\n\nВ последнем скане подходящих кандидатов нет."
    lines = [f"🟡 <b>{html.escape(spec.title.upper())} — КАНДИДАТЫ</b>"]
    for x in rows:
        lines.append(
            f"\n<b>{html.escape(x.get('symbol',''))}</b> · {x.get('direction','—')} · {x.get('status')} · Score {float(x.get('score') or 0):.0f}\n"
            f"Entry {_p(x.get('entry_price'))} · RR {float(x.get('rr') or 0):.2f}\n"
            f"{html.escape(str(x.get('reason') or '—'))}"
        )
    return "\n".join(lines)


def winrate_text(strategy: str = "fib_05_pullback"):
    spec = get_strategy(strategy)
    s = stats(spec.key)
    pf = s["profit_factor"]
    pf_text = "∞" if pf >= 999 else f"{pf:.2f}"
    lines = [
        f"📈 <b>{html.escape(spec.title.upper())} — СТАТИСТИКА</b>",
        f"Всего READY-setups: <b>{s['total']}</b>",
        f"Завершено: <b>{s['resolved']}</b>",
        f"Побед / убытков: <b>{s['wins']} / {s['losses']}</b>",
        f"Win Rate: <b>{s['win_rate']:.1f}%</b>",
        f"Profit Factor: <b>{pf_text}</b>",
        f"Expectancy / средний return: <b>{s['expectancy']:+.2f}%</b>",
        "",
        f"Ожидают entry: {s['waiting']} · Открыты: {s['open']} · Expired: {s['expired']}",
    ]
    if s["resolved"] < 30:
        lines += ["", "⚠️ Выборка пока мала. Сравнивать стратегии разумнее после 30–50+ завершённых setups на каждую."]
    return "\n".join(lines)


def history_text(strategy: str = "fib_05_pullback"):
    spec = get_strategy(strategy)
    s = stats(spec.key)
    rows = s.get("recent") or []
    if not rows:
        return f"📜 <b>{html.escape(spec.title)} — ИСТОРИЯ</b>\n\nИстория пока пуста."
    lines = [f"📜 <b>{html.escape(spec.title.upper())} — ИСТОРИЯ</b>"]
    for x in rows[:12]:
        state = x.get("state") or "—"
        ret = x.get("return_pct")
        suffix = f" · {float(ret):+.2f}%" if ret is not None else ""
        lines.append(
            f"\n<b>{html.escape(x.get('symbol',''))}</b> · {x.get('direction','—')} · {state}{suffix}\n"
            f"Entry {_p(x.get('entry_price'))} · RR {float(x.get('rr') or 0):.2f}"
        )
    return "\n".join(lines)


def rules_text(strategy: str = "fib_05_pullback"):
    spec = get_strategy(strategy)
    lines = [f"📐 <b>ПРАВИЛА — {html.escape(spec.title.upper())}</b>", ""]
    for i, rule in enumerate(spec.rules, 1):
        lines.append(f"{i}. {html.escape(rule)}")
    lines += [
        "",
        "Forward-tracking:",
        "• READY сначала сохраняется как waiting_entry.",
        "• Вход учитывается только после будущего касания/trigger согласно entry mode.",
        "• Entry + SL/TP в одной 1H свече обрабатывается консервативно: при неоднозначности считается SL.",
        "• Эти результаты не открывают основной Paper автоматически.",
    ]
    return "\n".join(lines)


def leaderboard_text():
    rows = leaderboard()
    lines = [
        "🏆 <b>STRATEGY LEADERBOARD</b>",
        "Сортировка по достаточности выборки, затем Profit Factor и expectancy.",
        "",
    ]
    any_data = False
    for idx, row in enumerate(rows, 1):
        if row.get("total", 0) <= 0:
            continue
        any_data = True
        pf = row.get("profit_factor", 0)
        pf_text = "∞" if pf >= 999 else f"{pf:.2f}"
        sample = "✅" if row.get("resolved", 0) >= 30 else "🧪"
        lines.append(
            f"{idx}. {row['emoji']} <b>{html.escape(row['title'])}</b> {sample}\n"
            f"   Trades {row['resolved']} · WR {row['win_rate']:.1f}% · PF {pf_text} · Exp {row['expectancy']:+.2f}%"
        )
    if not any_data:
        lines.append("Пока нет завершённых strategy setups. Запускай анализы и накапливай forward-выборку.")
    lines += ["", "🧪 = выборка < 30 завершённых setups; рейтинг пока предварительный."]
    return "\n".join(lines)


# Backward compatibility with v25 Fib-specific imports.
def fib_home_text():
    return strategy_detail_text("fib_05_pullback")


def fib_scan_report(result):
    return scan_report(result, "fib_05_pullback")
