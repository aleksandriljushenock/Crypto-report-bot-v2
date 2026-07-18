def gap_size_percent(low, high):
    midpoint = (low + high) / 2

    if midpoint == 0:
        return 0.0

    return abs(high - low) / midpoint * 100


def detect_fvg(candles, min_gap_percent=0.08, lookback=150):
    if not isinstance(candles, list) or len(candles) < 10:
        return []

    closed = candles[:-1]
    start_index = max(2, len(closed) - lookback)

    gaps = []

    for index in range(start_index, len(closed)):
        first = closed[index - 2]
        middle = closed[index - 1]
        third = closed[index]

        bullish_gap_low = first["high"]
        bullish_gap_high = third["low"]

        if bullish_gap_high > bullish_gap_low:
            size_percent = gap_size_percent(
                bullish_gap_low,
                bullish_gap_high,
            )

            if size_percent >= min_gap_percent:
                gaps.append({
                    "type": "BULLISH_FVG",
                    "index": index,
                    "time": third["open_time"],
                    "low": bullish_gap_low,
                    "high": bullish_gap_high,
                    "midpoint": round(
                        (
                            bullish_gap_low
                            + bullish_gap_high
                        ) / 2,
                        8,
                    ),
                    "sizePercent": round(
                        size_percent,
                        4,
                    ),
                    "displacementCandle": {
                        "open": middle["open"],
                        "high": middle["high"],
                        "low": middle["low"],
                        "close": middle["close"],
                    },
                })

        bearish_gap_low = third["high"]
        bearish_gap_high = first["low"]

        if bearish_gap_high > bearish_gap_low:
            size_percent = gap_size_percent(
                bearish_gap_low,
                bearish_gap_high,
            )

            if size_percent >= min_gap_percent:
                gaps.append({
                    "type": "BEARISH_FVG",
                    "index": index,
                    "time": third["open_time"],
                    "low": bearish_gap_low,
                    "high": bearish_gap_high,
                    "midpoint": round(
                        (
                            bearish_gap_low
                            + bearish_gap_high
                        ) / 2,
                        8,
                    ),
                    "sizePercent": round(
                        size_percent,
                        4,
                    ),
                    "displacementCandle": {
                        "open": middle["open"],
                        "high": middle["high"],
                        "low": middle["low"],
                        "close": middle["close"],
                    },
                })

    return gaps


def analyze_fill_status(gap, candles_after, last_price):
    gap_low = gap["low"]
    gap_high = gap["high"]
    midpoint = gap["midpoint"]

    touched = False
    midpoint_filled = False
    fully_filled = False
    invalidated = False

    for candle in candles_after:
        if gap["type"] == "BULLISH_FVG":
            if candle["low"] <= gap_high:
                touched = True

            if candle["low"] <= midpoint:
                midpoint_filled = True

            if candle["low"] <= gap_low:
                fully_filled = True

            if candle["close"] < gap_low:
                invalidated = True

        elif gap["type"] == "BEARISH_FVG":
            if candle["high"] >= gap_low:
                touched = True

            if candle["high"] >= midpoint:
                midpoint_filled = True

            if candle["high"] >= gap_high:
                fully_filled = True

            if candle["close"] > gap_high:
                invalidated = True

    inside_now = gap_low <= last_price <= gap_high

    if invalidated:
        status = "INVALIDATED"
    elif fully_filled:
        status = "FILLED"
    elif inside_now:
        status = "IN_FVG"
    elif midpoint_filled:
        status = "MIDPOINT_FILLED"
    elif touched:
        status = "PARTIALLY_FILLED"
    else:
        status = "UNFILLED"

    active = not invalidated and not fully_filled

    return {
        "active": active,
        "status": status,
        "touched": touched,
        "midpointFilled": midpoint_filled,
        "fullyFilled": fully_filled,
        "insideNow": inside_now,
        "invalidated": invalidated,
    }


def distance_percent(price, low, high):
    if price == 0:
        return None

    if low <= price <= high:
        return 0.0

    if price < low:
        distance = low - price
    else:
        distance = price - high

    return round(distance / price * 100, 4)


def enrich_fvgs(candles, gaps):
    closed = candles[:-1]
    last_price = candles[-1]["close"]

    result = []

    for gap in gaps:
        candles_after = closed[gap["index"] + 1:]

        fill = analyze_fill_status(
            gap,
            candles_after,
            last_price,
        )

        item = dict(gap)
        item.update(fill)

        item["distancePercent"] = distance_percent(
            last_price,
            gap["low"],
            gap["high"],
        )

        result.append(item)

    return result


def choose_nearest_active(gaps, gap_type):
    candidates = [
        gap
        for gap in gaps
        if gap["type"] == gap_type
        and gap["active"]
    ]

    if not candidates:
        return None

    candidates.sort(
        key=lambda gap: (
            gap["distancePercent"]
            if gap["distancePercent"] is not None
            else float("inf")
        )
    )

    return candidates[0]


def analyze_fvg(candles):
    if not isinstance(candles, list) or len(candles) < 20:
        return {
            "available": False,
            "error": "Недостаточно свечей для FVG",
        }

    raw_gaps = detect_fvg(candles)
    gaps = enrich_fvgs(candles, raw_gaps)

    nearest_bullish = choose_nearest_active(
        gaps,
        "BULLISH_FVG",
    )

    nearest_bearish = choose_nearest_active(
        gaps,
        "BEARISH_FVG",
    )

    active_gaps = [
        gap
        for gap in gaps
        if gap["active"]
    ]

    inside_bullish = any(
        gap["type"] == "BULLISH_FVG"
        and gap["insideNow"]
        for gap in active_gaps
    )

    inside_bearish = any(
        gap["type"] == "BEARISH_FVG"
        and gap["insideNow"]
        for gap in active_gaps
    )

    if inside_bullish and not inside_bearish:
        context = "IN_BULLISH_FVG"
    elif inside_bearish and not inside_bullish:
        context = "IN_BEARISH_FVG"
    elif nearest_bullish and nearest_bearish:
        if (
            nearest_bullish["distancePercent"]
            < nearest_bearish["distancePercent"]
        ):
            context = "BULLISH_FVG_NEAREST"
        else:
            context = "BEARISH_FVG_NEAREST"
    elif nearest_bullish:
        context = "BULLISH_FVG_NEAREST"
    elif nearest_bearish:
        context = "BEARISH_FVG_NEAREST"
    else:
        context = "NO_ACTIVE_FVG"

    return {
        "available": True,
        "context": context,
        "activeCount": len(active_gaps),
        "nearestBullish": nearest_bullish,
        "nearestBearish": nearest_bearish,
        "gaps": gaps[-10:],
    }


def analyze_fvg_multiple_timeframes(parsed_klines):
    result = {}

    for interval in ("15m", "1h", "4h"):
        result[interval] = analyze_fvg(
            parsed_klines.get(interval, [])
        )

    return result