def candle_body(candle):
    return abs(candle["close"] - candle["open"])


def candle_range(candle):
    return candle["high"] - candle["low"]


def is_bullish(candle):
    return candle["close"] > candle["open"]


def is_bearish(candle):
    return candle["close"] < candle["open"]


def average_body(candles):
    if not candles:
        return 0.0

    return sum(candle_body(candle) for candle in candles) / len(candles)


def zone_distance_percent(price, zone_low, zone_high):
    if price == 0:
        return None

    if zone_low <= price <= zone_high:
        return 0.0

    if price < zone_low:
        distance = zone_low - price
    else:
        distance = price - zone_high

    return round(distance / price * 100, 4)


def detect_mitigation(zone, candles_after, last_price):
    zone_low = zone["low"]
    zone_high = zone["high"]

    touched = False
    invalidated = False
    first_touch_index = None

    for index, candle in enumerate(candles_after):
        overlaps = (
            candle["low"] <= zone_high
            and candle["high"] >= zone_low
        )

        if overlaps and not touched:
            touched = True
            first_touch_index = index

        if zone["type"] == "BULLISH_OB":
            if candle["close"] < zone_low:
                invalidated = True

        elif zone["type"] == "BEARISH_OB":
            if candle["close"] > zone_high:
                invalidated = True

    active = not invalidated

    inside_now = zone_low <= last_price <= zone_high

    if invalidated:
        status = "INVALIDATED"
    elif inside_now:
        status = "IN_ZONE"
    elif touched:
        status = "MITIGATED"
    else:
        status = "UNMITIGATED"

    return {
        "active": active,
        "touched": touched,
        "insideNow": inside_now,
        "invalidated": invalidated,
        "status": status,
        "firstTouchOffset": first_touch_index,
    }


def find_order_blocks(
    candles,
    lookback=120,
    impulse_window=3,
    impulse_body_multiplier=1.5,
    displacement_percent=0.35,
):
    if not isinstance(candles, list) or len(candles) < 30:
        return []

    closed = candles[:-1]

    if len(closed) < 20:
        return []

    start_index = max(5, len(closed) - lookback)
    blocks = []

    for index in range(start_index, len(closed) - impulse_window):
        candidate = closed[index]

        previous = closed[max(0, index - 20):index]
        avg_body = average_body(previous)

        if avg_body <= 0:
            continue

        future = closed[index + 1:index + 1 + impulse_window]

        if not future:
            continue

        future_high = max(candle["high"] for candle in future)
        future_low = min(candle["low"] for candle in future)
        future_close = future[-1]["close"]

        candidate_range = candle_range(candidate)

        if candidate_range <= 0:
            continue

        bullish_displacement = (
            future_high - candidate["high"]
        ) / candidate["close"] * 100

        bearish_displacement = (
            candidate["low"] - future_low
        ) / candidate["close"] * 100

        bullish_impulse = (
            future_close > candidate["high"]
            and bullish_displacement >= displacement_percent
            and any(
                is_bullish(candle)
                and candle_body(candle) >= avg_body * impulse_body_multiplier
                for candle in future
            )
        )

        bearish_impulse = (
            future_close < candidate["low"]
            and bearish_displacement >= displacement_percent
            and any(
                is_bearish(candle)
                and candle_body(candle) >= avg_body * impulse_body_multiplier
                for candle in future
            )
        )

        if is_bearish(candidate) and bullish_impulse:
            zone = {
                "type": "BULLISH_OB",
                "index": index,
                "time": candidate["open_time"],
                "low": candidate["low"],
                "high": candidate["open"],
                "fullCandleLow": candidate["low"],
                "fullCandleHigh": candidate["high"],
                "impulsePercent": round(
                    bullish_displacement,
                    4,
                ),
            }

            blocks.append(zone)

        elif is_bullish(candidate) and bearish_impulse:
            zone = {
                "type": "BEARISH_OB",
                "index": index,
                "time": candidate["open_time"],
                "low": candidate["open"],
                "high": candidate["high"],
                "fullCandleLow": candidate["low"],
                "fullCandleHigh": candidate["high"],
                "impulsePercent": round(
                    bearish_displacement,
                    4,
                ),
            }

            blocks.append(zone)

    return blocks


def enrich_order_blocks(candles, blocks):
    if not blocks:
        return []

    closed = candles[:-1]
    last_price = candles[-1]["close"]
    enriched = []

    for block in blocks:
        candles_after = closed[block["index"] + 1:]

        mitigation = detect_mitigation(
            block,
            candles_after,
            last_price,
        )

        item = dict(block)
        item.update(mitigation)

        item["midpoint"] = round(
            (block["low"] + block["high"]) / 2,
            8,
        )

        item["distancePercent"] = zone_distance_percent(
            last_price,
            block["low"],
            block["high"],
        )

        enriched.append(item)

    return enriched


def choose_nearest_active(blocks, block_type, last_price):
    candidates = [
        block
        for block in blocks
        if block["type"] == block_type
        and block["active"]
    ]

    if not candidates:
        return None

    candidates.sort(
        key=lambda block: (
            block["distancePercent"]
            if block["distancePercent"] is not None
            else float("inf")
        )
    )

    return candidates[0]


def analyze_order_blocks(candles):
    if not isinstance(candles, list) or len(candles) < 30:
        return {
            "available": False,
            "error": "Недостаточно свечей для Order Block",
        }

    raw_blocks = find_order_blocks(candles)
    blocks = enrich_order_blocks(candles, raw_blocks)

    last_price = candles[-1]["close"]

    nearest_bullish = choose_nearest_active(
        blocks,
        "BULLISH_OB",
        last_price,
    )

    nearest_bearish = choose_nearest_active(
        blocks,
        "BEARISH_OB",
        last_price,
    )

    active_blocks = [
        block
        for block in blocks
        if block["active"]
    ]

    inside_bullish = any(
        block["type"] == "BULLISH_OB"
        and block["insideNow"]
        for block in active_blocks
    )

    inside_bearish = any(
        block["type"] == "BEARISH_OB"
        and block["insideNow"]
        for block in active_blocks
    )

    if inside_bullish and not inside_bearish:
        context = "IN_BULLISH_OB"
    elif inside_bearish and not inside_bullish:
        context = "IN_BEARISH_OB"
    elif nearest_bullish and nearest_bearish:
        if (
            nearest_bullish["distancePercent"]
            < nearest_bearish["distancePercent"]
        ):
            context = "BULLISH_OB_NEAREST"
        else:
            context = "BEARISH_OB_NEAREST"
    elif nearest_bullish:
        context = "BULLISH_OB_NEAREST"
    elif nearest_bearish:
        context = "BEARISH_OB_NEAREST"
    else:
        context = "NO_ACTIVE_OB"

    return {
        "available": True,
        "context": context,
        "lastPrice": last_price,
        "activeCount": len(active_blocks),
        "nearestBullish": nearest_bullish,
        "nearestBearish": nearest_bearish,
        "blocks": blocks[-10:],
    }


def analyze_order_blocks_multiple_timeframes(parsed_klines):
    result = {}

    for interval in ("15m", "1h", "4h"):
        result[interval] = analyze_order_blocks(
            parsed_klines.get(interval, [])
        )

    return result