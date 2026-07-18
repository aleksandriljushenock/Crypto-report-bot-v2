def get_final_status(item):
    rules = item.get("rules", {})
    return rules.get("finalStatus", item["score"]["status"])


def get_action(item):
    return item.get("rules", {}).get("action", "WAIT")


def get_reason(item):
    return item.get("rules", {}).get("mainReason", "нужно подтверждение")


def get_market_state(rows):
    trade = [r for r in rows if get_final_status(r) == "TRADE_CANDIDATE"]
    watch = [r for r in rows if get_final_status(r) == "WATCH"]
    skip = [r for r in rows if get_final_status(r) == "SKIP"]

    if len(trade) >= 3:
        return "🟢 BULLISH", "Можно искать сделки, но только по подтвержденным уровням."

    if len(watch) >= len(skip):
        return "🟡 NEUTRAL", "Лучше ждать подтверждений. Не входить с рынка."

    return "🔴 WEAK", "Рынок слабый. Приоритет — пропуск сделок."


def coin_title(rank):
    if rank == 1:
        return "🥇"
    if rank == 2:
        return "🥈"
    if rank == 3:
        return "🥉"
    return f"{rank}."


def build_coin_plan(item, rank):
    s = item["score"]
    levels = item.get("levels")
    rules = item.get("rules", {})

    symbol = s["symbol"]
    direction = s.get("direction", "NO_TRADE")
    action = rules.get("action", "WAIT")
    reason = rules.get(
        "mainReason",
        "нужно дополнительное подтверждение",
    )
    best_setup = rules.get("bestSetup", "NONE")

    # ---------------------------------------------
    # SMC
    # ---------------------------------------------

    smc = s.get("smcAnalysis", {})

    smc_15m = smc.get("15m", {})
    smc_1h = smc.get("1h", {})
    smc_4h = smc.get("4h", {})

    # ---------------------------------------------
    # ORDER BLOCKS
    # ---------------------------------------------

    order_blocks = s.get(
        "orderBlockAnalysis",
        {},
    )

    ob_15m = order_blocks.get("15m", {})
    ob_1h = order_blocks.get("1h", {})

    nearest_bullish_ob_15m = ob_15m.get(
        "nearestBullish"
    )
    nearest_bearish_ob_15m = ob_15m.get(
        "nearestBearish"
    )

    nearest_bullish_ob_1h = ob_1h.get(
        "nearestBullish"
    )
    nearest_bearish_ob_1h = ob_1h.get(
        "nearestBearish"
    )

    # ---------------------------------------------
    # FAIR VALUE GAPS
    # ---------------------------------------------

    fvg_analysis = s.get(
        "fvgAnalysis",
        {},
    )

    fvg_15m = fvg_analysis.get("15m", {})
    fvg_1h = fvg_analysis.get("1h", {})

    nearest_bullish_fvg_15m = fvg_15m.get(
        "nearestBullish"
    )
    nearest_bearish_fvg_15m = fvg_15m.get(
        "nearestBearish"
    )

    nearest_bullish_fvg_1h = fvg_1h.get(
        "nearestBullish"
    )
    nearest_bearish_fvg_1h = fvg_1h.get(
        "nearestBearish"
    )

    # ---------------------------------------------
    # OPEN INTEREST
    # ---------------------------------------------

    oi = s.get("oiAnalysis", {})

    oi_comment = oi.get("comment")
    oi_1h = oi.get("oiChange1h")
    oi_4h = oi.get("oiChange4h")
    oi_24h = oi.get("oiChange24h")

    # ---------------------------------------------
    # FUNDING
    # ---------------------------------------------

    funding_info = s.get(
        "fundingAnalysis",
        {},
    )

    funding_comment = funding_info.get("comment")
    current_funding = funding_info.get(
        "currentFunding"
    )
    average_funding = funding_info.get(
        "avgFunding24h"
    )

    # ---------------------------------------------
    # RELATIVE STRENGTH
    # ---------------------------------------------

    rs = s.get("relativeStrength", {})

    rs_label = rs.get("label")
    rs_details = rs.get("details", {})

    rs_15m = (
        rs_details
        .get("15m", {})
        .get("relativeStrength")
    )

    rs_1h = (
        rs_details
        .get("1h", {})
        .get("relativeStrength")
    )

    rs_4h = (
        rs_details
        .get("4h", {})
        .get("relativeStrength")
    )

    # ---------------------------------------------
    # ВЫБОР ЗОНЫ ПО НАПРАВЛЕНИЮ
    # ---------------------------------------------

    selected_ob_15m = None
    selected_ob_1h = None
    selected_fvg_15m = None
    selected_fvg_1h = None

    if direction == "LONG_BIAS":
        selected_ob_15m = nearest_bullish_ob_15m
        selected_ob_1h = nearest_bullish_ob_1h

        selected_fvg_15m = nearest_bullish_fvg_15m
        selected_fvg_1h = nearest_bullish_fvg_1h

    elif direction == "SHORT_BIAS":
        selected_ob_15m = nearest_bearish_ob_15m
        selected_ob_1h = nearest_bearish_ob_1h

        selected_fvg_15m = nearest_bearish_fvg_15m
        selected_fvg_1h = nearest_bearish_fvg_1h

    # ---------------------------------------------
    # ССЫЛКИ
    # ---------------------------------------------

    binance_url = (
        f"https://www.binance.com/en/futures/{symbol}"
    )

    tradingview_url = (
        "https://www.tradingview.com/chart/"
        f"?symbol=BINANCE:{symbol}"
    )

    # ---------------------------------------------
    # ФОРМИРОВАНИЕ TELEGRAM-БЛОКА
    # ---------------------------------------------

    lines = []

    lines.append(
        f"{coin_title(rank)} <b>{symbol}</b>"
    )

    lines.append(
        f"<b>Действие:</b> {action}"
    )

    lines.append(
        f"<b>Направление:</b> {direction}"
    )

    lines.append(
        f"<b>Причина:</b> {reason}"
    )

    # ---------------------------------------------
    # SMC
    # ---------------------------------------------

    if smc_1h.get("available"):
        high_structure = smc_1h.get(
            "highStructure",
            "UNKNOWN",
        )

        low_structure = smc_1h.get(
            "lowStructure",
            "UNKNOWN",
        )

        event = smc_1h.get(
            "event",
            "NONE",
        )

        event_direction = smc_1h.get(
            "eventDirection",
            "NONE",
        )

        lines.append(
            f"<b>SMC 1H:</b> "
            f"{high_structure}/{low_structure} | "
            f"{event} {event_direction}"
        )

    if smc_4h.get("available"):
        lines.append(
            f"<b>SMC 4H:</b> "
            f"{smc_4h.get('bias')} | "
            f"{smc_4h.get('trend')}"
        )

    sweep = smc_15m.get(
        "liquiditySweep",
        {},
    )

    if sweep.get("found"):
        lines.append(
            f"<b>Liquidity:</b> "
            f"{sweep.get('type')} @ "
            f"<code>{sweep.get('level')}</code>"
        )

    # ---------------------------------------------
    # ORDER BLOCK 15M
    # ---------------------------------------------

    if selected_ob_15m:
        lines.append(
            f"<b>Order Block 15M:</b> "
            f"<code>{selected_ob_15m.get('low')}"
            f"–{selected_ob_15m.get('high')}</code> | "
            f"{selected_ob_15m.get('status')}"
        )

        distance = selected_ob_15m.get(
            "distancePercent"
        )

        if distance is not None:
            lines.append(
                f"<b>OB distance:</b> {distance}%"
            )

    # ---------------------------------------------
    # ORDER BLOCK 1H
    # ---------------------------------------------

    if selected_ob_1h:
        lines.append(
            f"<b>Order Block 1H:</b> "
            f"<code>{selected_ob_1h.get('low')}"
            f"–{selected_ob_1h.get('high')}</code> | "
            f"{selected_ob_1h.get('status')}"
        )

    # ---------------------------------------------
    # FVG 15M
    # ---------------------------------------------

    if selected_fvg_15m:
        lines.append(
            f"<b>FVG 15M:</b> "
            f"<code>{selected_fvg_15m.get('low')}"
            f"–{selected_fvg_15m.get('high')}</code> | "
            f"{selected_fvg_15m.get('status')}"
        )

        distance = selected_fvg_15m.get(
            "distancePercent"
        )

        if distance is not None:
            lines.append(
                f"<b>FVG distance:</b> {distance}%"
            )

    # ---------------------------------------------
    # FVG 1H
    # ---------------------------------------------

    if selected_fvg_1h:
        lines.append(
            f"<b>FVG 1H:</b> "
            f"<code>{selected_fvg_1h.get('low')}"
            f"–{selected_fvg_1h.get('high')}</code> | "
            f"{selected_fvg_1h.get('status')}"
        )

    # ---------------------------------------------
    # RELATIVE STRENGTH
    # ---------------------------------------------

    if rs_label:
        lines.append(
            f"<b>RS к BTC:</b> {rs_label} | "
            f"1h: {rs_15m}% | "
            f"4h: {rs_1h}% | "
            f"24h: {rs_4h}%"
        )

    # ---------------------------------------------
    # OPEN INTEREST
    # ---------------------------------------------

    if oi_comment:
        lines.append(
            f"<b>OI:</b> {oi_comment} | "
            f"1h: {oi_1h}% | "
            f"4h: {oi_4h}% | "
            f"24h: {oi_24h}%"
        )

    # ---------------------------------------------
    # FUNDING
    # ---------------------------------------------

    if funding_comment:
        lines.append(
            f"<b>Funding:</b> {funding_comment} | "
            f"now: {current_funding}% | "
            f"avg: {average_funding}%"
        )

    # ---------------------------------------------
    # ТОРГОВЫЙ ПЛАН
    # ---------------------------------------------

    if levels:
        lines.append("")

        if best_setup == "PULLBACK":
            lines.append(
                "<b>План:</b> ждать откат"
            )

            entry_zone = levels.get(
                "pullbackEntryZone"
            )

            if entry_zone:
                lines.append(
                    f"Entry: "
                    f"<code>{entry_zone[0]}"
                    f"–{entry_zone[1]}</code>"
                )

            lines.append(
                f"SL: "
                f"<code>{levels.get('pullbackStop')}</code>"
            )

            lines.append(
                f"R/R: "
                f"<code>{levels.get('pullbackRR')}</code>"
            )

        elif best_setup == "BREAKOUT":
            lines.append(
                "<b>План:</b> ждать пробой"
            )

            lines.append(
                f"Entry: "
                f"<code>{levels.get('breakoutEntry')}</code>"
            )

            lines.append(
                f"SL: "
                f"<code>{levels.get('breakoutStop')}</code>"
            )

            lines.append(
                f"R/R: "
                f"<code>{levels.get('breakoutRR')}</code>"
            )

        else:
            lines.append(
                "<b>План:</b> ждать подтверждение"
            )

        lines.append(
            f"TP: "
            f"<code>{levels.get('tp1')}</code> / "
            f"<code>{levels.get('tp2')}</code> / "
            f"<code>{levels.get('tp3')}</code>"
        )

    # ---------------------------------------------
    # УСЛОВИЯ
    # ---------------------------------------------

    reasons_to_watch = rules.get(
        "reasonsToWatch",
        [],
    )

    reasons_to_skip = rules.get(
        "reasonsToSkip",
        [],
    )

    confirmations = rules.get(
        "confirmations",
        [],
    )

    if confirmations:
        lines.append("")
        lines.append("<b>Подтверждения:</b>")

        for confirmation in confirmations[:3]:
            lines.append(
                f"✓ {confirmation}"
            )

    if reasons_to_watch:
        lines.append("")
        lines.append("<b>Что должно произойти:</b>")

        for watch_reason in reasons_to_watch[:2]:
            lines.append(
                f"• {watch_reason}"
            )

    if reasons_to_skip:
        lines.append("")
        lines.append("<b>Что отменяет сценарий:</b>")

        for skip_reason in reasons_to_skip[:2]:
            lines.append(
                f"✕ {skip_reason}"
            )

    # ---------------------------------------------
    # ССЫЛКИ
    # ---------------------------------------------

    lines.append("")

    lines.append(
        f'<a href="{binance_url}">Binance</a> | '
        f'<a href="{tradingview_url}">TradingView</a>'
    )

    return "\n".join(lines)

def build_brief_report(rows, run_time, ai_text=None):
    trade = [r for r in rows if get_final_status(r) == "TRADE_CANDIDATE"]
    watch = [r for r in rows if get_final_status(r) == "WATCH"]
    skip = [r for r in rows if get_final_status(r) == "SKIP"]

    candidates = trade[:3] if trade else watch[:3]

    market_state, market_comment = get_market_state(rows)

    lines = []
    lines.append("<b>📊 CRYPTO BRIEF</b>")
    lines.append(f"<b>UTC:</b> {run_time}")
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("<b>🌍 РЫНОК</b>")
    lines.append(market_state)
    lines.append(market_comment)

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("<b>🎯 СЕГОДНЯ В ФОКУСЕ</b>")

    if not candidates:
        lines.append("Качественных сетапов нет. Лучшее действие — ждать.")
    else:
        for i, item in enumerate(candidates, start=1):
            lines.append("")
            lines.append(build_coin_plan(item, i))
            lines.append("━━━━━━━━━━━━━━━━━━━━")

    if ai_text:
        lines.append("")
        lines.append("<b>🤖 AI</b>")
        lines.append(ai_text[:700])

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("<b>📌 ИТОГ</b>")

    if trade:
        lines.append("✅ Есть кандидаты, но вход только по условиям.")
    else:
        lines.append("❌ Сейчас не входить с рынка.")

    if candidates:
        symbols = ", ".join([item["score"]["symbol"].replace("USDT", "") for item in candidates])
        lines.append(f"👀 Следить: {symbols}")

    lines.append(f"🚫 Пропустить: {len(skip)} монет.")
    lines.append("<b>Risk:</b> 0.5–1% капитала.")

    return "\n".join(lines)