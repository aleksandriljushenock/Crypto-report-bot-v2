def evaluate_rules(score, levels):
    reasons_to_skip = []
    reasons_to_watch = []
    confirmations = []

    symbol = score["symbol"]

    volume = score["quoteVolume"]
    direction = score["direction"]
    status = score["status"]
    structure_15m = score["structure15m"]
    structure_1h = score["structure1h"]
    funding = score["fundingPercent"]
    taker = score["takerBuySellRatio"]
    rsi = score.get("rsi1h")
    ema20 = score.get("ema20_1h")
    ema50 = score.get("ema50_1h")
    ema200 = score.get("ema200_1h")
    vwap = score.get("vwap15m")
    atr1h = score["atr1hPercent"]

    # Relative Strength
    relative_strength = score.get("relativeStrength", {})
    rs_label = relative_strength.get("label")

    # Open Interest
    oi = score.get("oiAnalysis", {})
    oi_label = oi.get("label")

    # Funding history
    funding_analysis = score.get("fundingAnalysis", {})
    funding_label = funding_analysis.get("label")

    # SMC
    smc = score.get("smcAnalysis", {})
    smc_15m = smc.get("15m", {})
    smc_1h = smc.get("1h", {})
    smc_4h = smc.get("4h", {})

    # Order Blocks
    order_blocks = score.get("orderBlockAnalysis", {})
    ob_15m = order_blocks.get("15m", {})
    ob_1h = order_blocks.get("1h", {})

    # Fair Value Gaps
    fvg = score.get("fvgAnalysis", {})
    fvg_15m = fvg.get("15m", {})
    fvg_1h = fvg.get("1h", {})

    # --------------------------------------------------
    # 1. ЖЕСТКИЕ ПРИЧИНЫ ДЛЯ SKIP
    # --------------------------------------------------

    if volume < 100_000_000:
        reasons_to_skip.append("24h volume ниже 100M USDT")

    if direction == "NO_TRADE":
        reasons_to_skip.append("нет направленного преимущества")

    if structure_1h == "BOS_DOWN" and direction == "LONG_BIAS":
        reasons_to_skip.append("1H структура медвежья против LONG")

    if structure_1h == "BOS_UP" and direction == "SHORT_BIAS":
        reasons_to_skip.append("1H структура бычья против SHORT")

    if funding > 0.05:
        reasons_to_skip.append("текущий funding перегрет")

    if funding_label == "FUNDING_OVERHEATED_LONG":
        reasons_to_skip.append(
            "funding history показывает перегрев лонгов"
        )

    if rsi is not None and rsi > 75 and direction == "LONG_BIAS":
        reasons_to_skip.append("RSI 1H перегрет для LONG")

    if rsi is not None and rsi < 25 and direction == "SHORT_BIAS":
        reasons_to_skip.append("RSI 1H перепродан для SHORT")

    if rs_label == "WEAK" and symbol != "BTCUSDT":
        reasons_to_skip.append("монета слабее BTC")

    # Старший SMC против направления
    if smc_4h.get("available"):
        if (
            direction == "LONG_BIAS"
            and smc_4h.get("bias") == "BEARISH"
        ):
            reasons_to_skip.append(
                "4H SMC-структура против LONG"
            )

        if (
            direction == "SHORT_BIAS"
            and smc_4h.get("bias") == "BULLISH"
        ):
            reasons_to_skip.append(
                "4H SMC-структура против SHORT"
            )

    # --------------------------------------------------
    # 2. ПРИЧИНЫ ЖДАТЬ
    # --------------------------------------------------

    if atr1h < 0.4:
        reasons_to_watch.append(
            "низкая волатильность для интрадей-сделки"
        )

    if structure_1h not in ["BOS_UP", "SWEEP_LOW"]:
        if direction == "LONG_BIAS":
            reasons_to_watch.append(
                "нет сильного 1H подтверждения LONG"
            )

    if structure_1h not in ["BOS_DOWN", "SWEEP_HIGH"]:
        if direction == "SHORT_BIAS":
            reasons_to_watch.append(
                "нет сильного 1H подтверждения SHORT"
            )

    if taker < 1 and direction == "LONG_BIAS":
        reasons_to_watch.append(
            "taker buy/sell не подтверждает покупателя"
        )

    if taker > 1 and direction == "SHORT_BIAS":
        reasons_to_watch.append(
            "taker buy/sell не подтверждает продавца"
        )

    if oi_label == "OI_DROPPING_FAST":
        reasons_to_watch.append(
            "OI резко падает — движение может быть закрытием позиций"
        )

    if smc_1h.get("available"):
        if smc_1h.get("event") == "NONE":
            reasons_to_watch.append("нет BOS/CHOCH на 1H")

        if smc_1h.get("bias") == "NEUTRAL":
            reasons_to_watch.append(
                "SMC-структура 1H нейтральная"
            )

    # --------------------------------------------------
    # 3. ORDER BLOCK И FVG КАК ФИЛЬТРЫ
    # --------------------------------------------------

    if direction == "LONG_BIAS":
        bearish_ob = ob_15m.get("nearestBearish")

        if (
            bearish_ob
            and bearish_ob.get("distancePercent") is not None
            and bearish_ob["distancePercent"] < 0.5
        ):
            reasons_to_watch.append(
                "рядом bearish Order Block 15M"
            )

        bearish_fvg = fvg_15m.get("nearestBearish")

        if (
            bearish_fvg
            and bearish_fvg.get("distancePercent") is not None
            and bearish_fvg["distancePercent"] < 0.5
        ):
            reasons_to_watch.append(
                "рядом bearish FVG 15M"
            )

    if direction == "SHORT_BIAS":
        bullish_ob = ob_15m.get("nearestBullish")

        if (
            bullish_ob
            and bullish_ob.get("distancePercent") is not None
            and bullish_ob["distancePercent"] < 0.5
        ):
            reasons_to_watch.append(
                "рядом bullish Order Block 15M"
            )

        bullish_fvg = fvg_15m.get("nearestBullish")

        if (
            bullish_fvg
            and bullish_fvg.get("distancePercent") is not None
            and bullish_fvg["distancePercent"] < 0.5
        ):
            reasons_to_watch.append(
                "рядом bullish FVG 15M"
            )

    # --------------------------------------------------
    # 4. ПОДТВЕРЖДЕНИЯ
    # --------------------------------------------------

    if ema20 and ema50 and ema20 > ema50:
        confirmations.append("EMA20 выше EMA50")

    if ema200 and ema20 and ema20 > ema200:
        confirmations.append("структура выше EMA200")

    if vwap:
        confirmations.append("VWAP рассчитан")

    if structure_15m in ["BOS_UP", "SWEEP_LOW"]:
        confirmations.append(
            f"15M структура: {structure_15m}"
        )

    if rs_label == "STRONG":
        confirmations.append("монета сильнее BTC")

    if oi_label in ["OI_GROWING_FAST", "OI_GROWING"]:
        confirmations.append(
            oi.get("comment", "OI растет")
        )

    if funding_label == "FUNDING_NEUTRAL":
        confirmations.append("Funding нейтральный")

    if smc_1h.get("event") == "BOS":
        confirmations.append(
            f"1H BOS {smc_1h.get('eventDirection')}"
        )

    if smc_1h.get("event") == "CHOCH":
        confirmations.append(
            f"1H CHOCH {smc_1h.get('eventDirection')}"
        )

    if smc_15m.get("liquiditySweep", {}).get("found"):
        sweep_type = smc_15m["liquiditySweep"].get("type")
        confirmations.append(
            f"15M liquidity sweep: {sweep_type}"
        )

    # Подтверждения Order Block / FVG
    if direction == "LONG_BIAS":
        if ob_15m.get("context") == "IN_BULLISH_OB":
            confirmations.append(
                "цена внутри bullish Order Block 15M"
            )

        if ob_1h.get("context") in [
            "IN_BULLISH_OB",
            "BULLISH_OB_NEAREST",
        ]:
            confirmations.append(
                "bullish Order Block поддерживает LONG"
            )

        if fvg_15m.get("context") == "IN_BULLISH_FVG":
            confirmations.append(
                "цена внутри bullish FVG 15M"
            )

        if fvg_1h.get("context") in [
            "IN_BULLISH_FVG",
            "BULLISH_FVG_NEAREST",
        ]:
            confirmations.append(
                "bullish FVG поддерживает LONG"
            )

    if direction == "SHORT_BIAS":
        if ob_15m.get("context") == "IN_BEARISH_OB":
            confirmations.append(
                "цена внутри bearish Order Block 15M"
            )

        if ob_1h.get("context") in [
            "IN_BEARISH_OB",
            "BEARISH_OB_NEAREST",
        ]:
            confirmations.append(
                "bearish Order Block поддерживает SHORT"
            )

        if fvg_15m.get("context") == "IN_BEARISH_FVG":
            confirmations.append(
                "цена внутри bearish FVG 15M"
            )

        if fvg_1h.get("context") in [
            "IN_BEARISH_FVG",
            "BEARISH_FVG_NEAREST",
        ]:
            confirmations.append(
                "bearish FVG поддерживает SHORT"
            )

    # --------------------------------------------------
    # 5. R/R И ВЫБОР ЛУЧШЕГО СЕТАПА
    # --------------------------------------------------

    rr_breakout = None
    rr_pullback = None
    best_setup = "NONE"

    if levels:
        rr_breakout = levels.get("breakoutRR")
        rr_pullback = levels.get("pullbackRR")

        valid_breakout = (
            rr_breakout is not None
            and rr_breakout >= 2
        )

        valid_pullback = (
            rr_pullback is not None
            and rr_pullback >= 2
        )

        if not valid_breakout and not valid_pullback:
            reasons_to_skip.append(
                f"R/R ниже 1:2: "
                f"breakout={rr_breakout}, "
                f"pullback={rr_pullback}"
            )

        elif valid_breakout and valid_pullback:
            best_setup = (
                "BREAKOUT"
                if rr_breakout >= rr_pullback
                else "PULLBACK"
            )

        elif valid_breakout:
            best_setup = "BREAKOUT"

        elif valid_pullback:
            best_setup = "PULLBACK"

    # --------------------------------------------------
    # 6. ПОДТВЕРЖДЕНИЕ SMC-НАПРАВЛЕНИЯ
    # --------------------------------------------------

    smc_direction_confirmed = False

    if direction == "LONG_BIAS":
        smc_direction_confirmed = (
            smc_1h.get("bias") == "BULLISH"
            or (
                smc_1h.get("event") == "CHOCH"
                and smc_1h.get("eventDirection") == "UP"
            )
            or (
                smc_15m.get("liquiditySweep", {}).get("type")
                == "SWEEP_LOW"
                and smc_15m.get("bias") != "BEARISH"
            )
        )

    elif direction == "SHORT_BIAS":
        smc_direction_confirmed = (
            smc_1h.get("bias") == "BEARISH"
            or (
                smc_1h.get("event") == "CHOCH"
                and smc_1h.get("eventDirection") == "DOWN"
            )
            or (
                smc_15m.get("liquiditySweep", {}).get("type")
                == "SWEEP_HIGH"
                and smc_15m.get("bias") != "BULLISH"
            )
        )

    # --------------------------------------------------
    # 7. ФИНАЛЬНОЕ РЕШЕНИЕ
    # --------------------------------------------------

    if reasons_to_skip:
        final_status = "SKIP"
        action = "SKIP"
        main_reason = reasons_to_skip[0]

    elif (
        status == "TRADE_CANDIDATE"
        and len(confirmations) >= 3
        and best_setup != "NONE"
        and smc_direction_confirmed
    ):
        final_status = "TRADE_CANDIDATE"
        action = "TRADE"
        main_reason = (
            f"есть подтверждения и рабочий сетап "
            f"{best_setup}"
        )

    else:
        final_status = "WATCH"
        action = "WAIT"

        if reasons_to_watch:
            main_reason = reasons_to_watch[0]
        else:
            main_reason = "нужно дополнительное подтверждение"

    return {
        "symbol": symbol,
        "finalStatus": final_status,
        "action": action,
        "mainReason": main_reason,
        "bestSetup": best_setup,
        "reasonsToSkip": reasons_to_skip,
        "reasonsToWatch": reasons_to_watch,
        "confirmations": confirmations,
        "rrBreakout": rr_breakout,
        "rrPullback": rr_pullback,
        "smcDirectionConfirmed": smc_direction_confirmed,
        "orderBlock15mContext": ob_15m.get("context"),
        "orderBlock1hContext": ob_1h.get("context"),
        "fvg15mContext": fvg_15m.get("context"),
        "fvg1hContext": fvg_1h.get("context"),
    }