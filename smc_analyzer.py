from typing import Any


def find_swings(candles, left=3, right=3):
    """
    Ищет подтвержденные swing high и swing low.

    Swing подтверждается только после появления right свечей справа,
    поэтому последние незакрытые экстремумы не используются.
    """
    swing_highs = []
    swing_lows = []

    if len(candles) < left + right + 1:
        return swing_highs, swing_lows

    for index in range(left, len(candles) - right):
        current_high = candles[index]["high"]
        current_low = candles[index]["low"]

        left_part = candles[index - left:index]
        right_part = candles[index + 1:index + right + 1]

        is_swing_high = (
            all(current_high > candle["high"] for candle in left_part)
            and all(current_high >= candle["high"] for candle in right_part)
        )

        is_swing_low = (
            all(current_low < candle["low"] for candle in left_part)
            and all(current_low <= candle["low"] for candle in right_part)
        )

        if is_swing_high:
            swing_highs.append({
                "index": index,
                "time": candles[index]["open_time"],
                "price": current_high,
            })

        if is_swing_low:
            swing_lows.append({
                "index": index,
                "time": candles[index]["open_time"],
                "price": current_low,
            })

    return swing_highs, swing_lows


def classify_swing_structure(swing_highs, swing_lows):
    high_structure = "UNKNOWN"
    low_structure = "UNKNOWN"

    if len(swing_highs) >= 2:
        previous_high = swing_highs[-2]["price"]
        current_high = swing_highs[-1]["price"]

        if current_high > previous_high:
            high_structure = "HH"
        elif current_high < previous_high:
            high_structure = "LH"
        else:
            high_structure = "EH"

    if len(swing_lows) >= 2:
        previous_low = swing_lows[-2]["price"]
        current_low = swing_lows[-1]["price"]

        if current_low > previous_low:
            low_structure = "HL"
        elif current_low < previous_low:
            low_structure = "LL"
        else:
            low_structure = "EL"

    if high_structure == "HH" and low_structure == "HL":
        trend = "BULLISH"
    elif high_structure == "LH" and low_structure == "LL":
        trend = "BEARISH"
    else:
        trend = "MIXED"

    return {
        "highStructure": high_structure,
        "lowStructure": low_structure,
        "trend": trend,
    }


def detect_equal_levels(swings, tolerance_percent=0.15):
    """
    Ищет два последних экстремума примерно на одном уровне.
    tolerance_percent задается в процентах.
    """
    if len(swings) < 2:
        return {
            "found": False,
            "level": None,
            "distancePercent": None,
        }

    first = swings[-2]["price"]
    second = swings[-1]["price"]

    average = (first + second) / 2

    if average == 0:
        return {
            "found": False,
            "level": None,
            "distancePercent": None,
        }

    distance_percent = abs(second - first) / average * 100
    found = distance_percent <= tolerance_percent

    return {
        "found": found,
        "level": round(average, 8) if found else None,
        "distancePercent": round(distance_percent, 4),
    }


def detect_break(last_closed, swing_highs, swing_lows, previous_trend):
    """
    Определяет BOS или CHOCH по закрытию свечи за последним swing-уровнем.
    """
    result = {
        "event": "NONE",
        "direction": "NONE",
        "brokenLevel": None,
    }

    if not last_closed:
        return result

    close = last_closed["close"]

    last_swing_high = swing_highs[-1]["price"] if swing_highs else None
    last_swing_low = swing_lows[-1]["price"] if swing_lows else None

    if last_swing_high is not None and close > last_swing_high:
        event = "CHOCH" if previous_trend == "BEARISH" else "BOS"

        return {
            "event": event,
            "direction": "UP",
            "brokenLevel": last_swing_high,
        }

    if last_swing_low is not None and close < last_swing_low:
        event = "CHOCH" if previous_trend == "BULLISH" else "BOS"

        return {
            "event": event,
            "direction": "DOWN",
            "brokenLevel": last_swing_low,
        }

    return result


def detect_liquidity_sweep(last_closed, swing_highs, swing_lows):
    """
    Sweep high:
    свеча прокалывает swing high, но закрывается ниже него.

    Sweep low:
    свеча прокалывает swing low, но закрывается выше него.
    """
    result = {
        "found": False,
        "type": "NONE",
        "level": None,
    }

    if not last_closed:
        return result

    last_swing_high = swing_highs[-1]["price"] if swing_highs else None
    last_swing_low = swing_lows[-1]["price"] if swing_lows else None

    if (
        last_swing_high is not None
        and last_closed["high"] > last_swing_high
        and last_closed["close"] < last_swing_high
    ):
        return {
            "found": True,
            "type": "SWEEP_HIGH",
            "level": last_swing_high,
        }

    if (
        last_swing_low is not None
        and last_closed["low"] < last_swing_low
        and last_closed["close"] > last_swing_low
    ):
        return {
            "found": True,
            "type": "SWEEP_LOW",
            "level": last_swing_low,
        }

    return result


def calculate_smc_score(
    structure,
    break_event,
    liquidity_sweep,
    equal_highs,
    equal_lows,
):
    bullish_score = 0
    bearish_score = 0

    if structure["trend"] == "BULLISH":
        bullish_score += 2
    elif structure["trend"] == "BEARISH":
        bearish_score += 2

    if break_event["event"] == "BOS" and break_event["direction"] == "UP":
        bullish_score += 3

    if break_event["event"] == "BOS" and break_event["direction"] == "DOWN":
        bearish_score += 3

    if break_event["event"] == "CHOCH" and break_event["direction"] == "UP":
        bullish_score += 4

    if break_event["event"] == "CHOCH" and break_event["direction"] == "DOWN":
        bearish_score += 4

    if liquidity_sweep["type"] == "SWEEP_LOW":
        bullish_score += 3

    if liquidity_sweep["type"] == "SWEEP_HIGH":
        bearish_score += 3

    if equal_highs["found"]:
        bearish_score += 1

    if equal_lows["found"]:
        bullish_score += 1

    if bullish_score > bearish_score:
        bias = "BULLISH"
    elif bearish_score > bullish_score:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"

    return {
        "bullishScore": bullish_score,
        "bearishScore": bearish_score,
        "bias": bias,
    }


def analyze_smc(candles, left=3, right=3):
    if not isinstance(candles, list) or len(candles) < 30:
        return {
            "available": False,
            "error": "Недостаточно свечей для SMC-анализа",
        }

    # Последняя свеча Binance может быть еще незакрытой.
    closed_candles = candles[:-1]

    if len(closed_candles) < 20:
        return {
            "available": False,
            "error": "Недостаточно закрытых свечей",
        }

    swing_highs, swing_lows = find_swings(
        closed_candles,
        left=left,
        right=right,
    )

    structure = classify_swing_structure(
        swing_highs,
        swing_lows,
    )

    last_closed = closed_candles[-1]

    break_event = detect_break(
        last_closed,
        swing_highs,
        swing_lows,
        structure["trend"],
    )

    liquidity_sweep = detect_liquidity_sweep(
        last_closed,
        swing_highs,
        swing_lows,
    )

    equal_highs = detect_equal_levels(swing_highs)
    equal_lows = detect_equal_levels(swing_lows)

    score = calculate_smc_score(
        structure,
        break_event,
        liquidity_sweep,
        equal_highs,
        equal_lows,
    )

    return {
        "available": True,
        "trend": structure["trend"],
        "highStructure": structure["highStructure"],
        "lowStructure": structure["lowStructure"],
        "event": break_event["event"],
        "eventDirection": break_event["direction"],
        "brokenLevel": break_event["brokenLevel"],
        "liquiditySweep": liquidity_sweep,
        "equalHighs": equal_highs,
        "equalLows": equal_lows,
        "lastSwingHigh": swing_highs[-1] if swing_highs else None,
        "lastSwingLow": swing_lows[-1] if swing_lows else None,
        "bullishScore": score["bullishScore"],
        "bearishScore": score["bearishScore"],
        "bias": score["bias"],
        "swingHighCount": len(swing_highs),
        "swingLowCount": len(swing_lows),
    }


def analyze_multiple_timeframes(parsed_klines):
    result = {}

    settings = {
        "15m": {"left": 3, "right": 3},
        "1h": {"left": 3, "right": 3},
        "4h": {"left": 2, "right": 2},
    }

    for interval, params in settings.items():
        candles = parsed_klines.get(interval, [])

        result[interval] = analyze_smc(
            candles,
            left=params["left"],
            right=params["right"],
        )

    return result