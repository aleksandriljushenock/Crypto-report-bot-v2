def format_volume(value):
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    return f"{value / 1_000_000:.1f}M"


def get_final_status(item):
    rules = item.get("rules")
    if rules:
        return rules.get("finalStatus", item["score"]["status"])
    return item["score"]["status"]


def get_action(item):
    return item.get("rules", {}).get("action", "WAIT")


def get_reason(item):
    return item.get("rules", {}).get("mainReason", "нужно подтверждение")


def build_one_line_setup(item, rank):
    s = item["score"]
    levels = item["levels"]
    rules = item.get("rules", {})
    symbol = s["symbol"]

    best_setup = rules.get("bestSetup", "NONE")
    reason = rules.get("mainReason", "нужно подтверждение")

    binance_url = f"https://www.binance.com/en/futures/{symbol}"
    tv_url = f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}"

    lines = []
    lines.append(f"<b>{rank}. {symbol}</b> — <b>{get_action(item)}</b> | {s['score']}/100")
    lines.append(f"<a href=\"{binance_url}\">Binance</a> | <a href=\"{tv_url}\">TV</a>")
    lines.append(f"Setup: <b>{best_setup}</b>")
    lines.append(f"Причина: {reason}")

    if levels:
        lines.append(
            f"Breakout: <code>{levels['breakoutEntry']}</code> / SL <code>{levels['breakoutStop']}</code> / RR <code>{levels.get('breakoutRR')}</code>"
        )
        lines.append(
            f"Pullback: <code>{levels['pullbackEntryZone'][0]}–{levels['pullbackEntryZone'][1]}</code> / SL <code>{levels['pullbackStop']}</code> / RR <code>{levels.get('pullbackRR')}</code>"
        )
        lines.append(
            f"TP: <code>{levels['tp1']}</code> / <code>{levels['tp2']}</code> / <code>{levels['tp3']}</code>"
        )

    return "\n".join(lines)


def build_pretty_report(rows, run_time):
    trade = [r for r in rows if get_final_status(r) == "TRADE_CANDIDATE"]
    watch = [r for r in rows if get_final_status(r) == "WATCH"]
    skip = [r for r in rows if get_final_status(r) == "SKIP"]

    candidates = trade[:3] if trade else watch[:3]

    lines = []
    lines.append("<b>📊 CRYPTO BRIEF</b>")
    lines.append(f"<b>UTC:</b> {run_time}")
    lines.append("")
    lines.append(f"🟢 Trade: <b>{len(trade)}</b> | 🟡 Watch: <b>{len(watch)}</b> | 🔴 Skip: <b>{len(skip)}</b>")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("<b>🔥 Главное сейчас</b>")

    if not candidates:
        lines.append("Качественных сетапов нет. Лучшее действие — ждать.")
    else:
        for i, item in enumerate(candidates, start=1):
            lines.append("")
            lines.append(build_one_line_setup(item, i))

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("<b>🟡 Watch кратко</b>")
    for item in watch[:5]:
        s = item["score"]
        lines.append(f"{s['symbol']}: {get_reason(item)}")

    if len(watch) > 5:
        lines.append(f"…еще {len(watch) - 5} в WATCH")

    lines.append("")
    lines.append("<b>🔴 Skip:</b> " + str(len(skip)) + " монет.")
    lines.append("<b>Risk:</b> 0.5–1% капитала. Не входить без подтверждения.")

    return "\n".join(lines)