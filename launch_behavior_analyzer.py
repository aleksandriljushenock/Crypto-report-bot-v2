def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_klines(raw_klines):
    candles = []

    if not isinstance(raw_klines, list):
        return candles

    for item in raw_klines:
        if not isinstance(item, list) or len(item) < 11:
            continue

        candles.append({
            "openTime": int(item[0]),
            "open": safe_float(item[1]),
            "high": safe_float(item[2]),
            "low": safe_float(item[3]),
            "close": safe_float(item[4]),
            "volume": safe_float(item[5]),
            "closeTime": int(item[6]),
            "quoteVolume": safe_float(item[7]),
            "tradeCount": int(item[8]),
            "takerBuyVolume": safe_float(item[9]),
            "takerBuyQuoteVolume": safe_float(item[10]),
        })

    return candles


def percent_change(current, previous):
    if previous in (None, 0):
        return None

    return (current - previous) / previous * 100


def calculate_vwap(candles):
    total_value = 0.0
    total_volume = 0.0

    for candle in candles:
        typical_price = (
            candle["high"]
            + candle["low"]
            + candle["close"]
        ) / 3

        volume = candle["volume"]

        total_value += typical_price * volume
        total_volume += volume

    if total_volume == 0:
        return None

    return total_value / total_volume


def calculate_relative_volume(candles, lookback=20):
    if len(candles) < lookback + 1:
        return None

    current_volume = candles[-1]["volume"]

    historical = [
        candle["volume"]
        for candle in candles[-lookback - 1:-1]
    ]

    average = sum(historical) / len(historical)

    if average == 0:
        return None

    return current_volume / average


def calculate_taker_ratio(candles, lookback=12):
    selected = candles[-lookback:]

    total_volume = sum(
        candle["volume"]
        for candle in selected
    )

    taker_buy = sum(
        candle["takerBuyVolume"]
        for candle in selected
    )

    taker_sell = total_volume - taker_buy

    if taker_sell <= 0:
        return None

    return taker_buy / taker_sell


def calculate_range_compression(candles, lookback=12):
    selected = candles[-lookback:]

    if len(selected) < lookback:
        return None

    high = max(
        candle["high"]
        for candle in selected
    )

    low = min(
        candle["low"]
        for candle in selected
    )

    close = selected[-1]["close"]

    if close == 0:
        return None

    return (high - low) / close * 100


def calculate_oi_change(history, lookback):
    if not isinstance(history, list):
        return None

    if len(history) <= lookback:
        return None

    values = [
        safe_float(
            item.get("sumOpenInterestValue")
        )
        for item in history
        if isinstance(item, dict)
    ]

    if len(values) <= lookback:
        return None

    return percent_change(
        values[-1],
        values[-1 - lookback],
    )


def analyze_launch_behavior(
    klines_5m,
    klines_1h,
    oi_history=None,
    current_funding=None,
):
    candles_5m = parse_klines(klines_5m)
    candles_1h = parse_klines(klines_1h)

    if len(candles_5m) < 20:
        return {
            "available": False,
            "score": 0,
            "action": "INSUFFICIENT_DATA",
            "actionLabel": "⚪ Недостаточно истории торгов",
            "reasonsFor": [],
            "reasonsAgainst": [
                "меньше 20 пятиминутных свечей"
            ],
        }

    first_price = candles_5m[0]["open"]
    current_price = candles_5m[-1]["close"]

    listing_high = max(
        candle["high"]
        for candle in candles_5m
    )

    listing_low = min(
        candle["low"]
        for candle in candles_5m
    )

    change_from_start = percent_change(
        current_price,
        first_price,
    )

    drawdown_from_high = percent_change(
        current_price,
        listing_high,
    )

    rise_from_low = percent_change(
        current_price,
        listing_low,
    )

    vwap = calculate_vwap(candles_5m)
    distance_from_vwap = (
        percent_change(current_price, vwap)
        if vwap
        else None
    )

    relative_volume = calculate_relative_volume(
        candles_5m
    )

    taker_ratio = calculate_taker_ratio(
        candles_5m
    )

    compression = calculate_range_compression(
        candles_5m
    )

    oi_change_1h = calculate_oi_change(
        oi_history,
        1,
    )

    oi_change_4h = calculate_oi_change(
        oi_history,
        4,
    )

    funding = safe_float(
        current_funding,
        None,
    )

    score = 50
    reasons_for = []
    reasons_against = []
    hard_reject = False

    # ------------------------------------------
    # Перегрев от стартовой цены
    # ------------------------------------------

    if change_from_start is not None:
        if change_from_start > 150:
            score -= 35
            hard_reject = True
            reasons_against.append(
                "цена выше старта более чем на 150%"
            )

        elif change_from_start > 80:
            score -= 25
            reasons_against.append(
                "сильный перегрев относительно старта"
            )

        elif change_from_start > 40:
            score -= 12
            reasons_against.append(
                "монета уже существенно выросла"
            )

        elif -15 <= change_from_start <= 30:
            score += 8
            reasons_for.append(
                "нет экстремального отрыва от стартовой цены"
            )

    # ------------------------------------------
    # Откат от листингового максимума
    # ------------------------------------------

    if drawdown_from_high is not None:
        if -35 <= drawdown_from_high <= -10:
            score += 12
            reasons_for.append(
                "после стартового пампа произошёл нормальный откат"
            )

        elif drawdown_from_high > -3:
            score -= 12
            reasons_against.append(
                "цена находится почти у листингового максимума"
            )

        elif drawdown_from_high < -65:
            score -= 15
            reasons_against.append(
                "слишком глубокая просадка от максимума"
            )

    # ------------------------------------------
    # VWAP
    # ------------------------------------------

    if distance_from_vwap is not None:
        if -3 <= distance_from_vwap <= 5:
            score += 12
            reasons_for.append(
                "цена находится около VWAP"
            )

        elif distance_from_vwap > 15:
            score -= 18
            reasons_against.append(
                "цена слишком далеко выше VWAP"
            )

        elif distance_from_vwap < -12:
            score -= 10
            reasons_against.append(
                "цена значительно ниже VWAP"
            )

    # ------------------------------------------
    # Объём
    # ------------------------------------------

    if relative_volume is not None:
        if 1.1 <= relative_volume <= 3:
            score += 8
            reasons_for.append(
                "объём подтверждает интерес"
            )

        elif relative_volume > 5:
            score -= 5
            reasons_against.append(
                "аномальный всплеск объёма"
            )

        elif relative_volume < 0.5:
            score -= 8
            reasons_against.append(
                "интерес и объём затухают"
            )

    # ------------------------------------------
    # Aggressive buyers / sellers
    # ------------------------------------------

    if taker_ratio is not None:
        if 1.05 <= taker_ratio <= 1.8:
            score += 7
            reasons_for.append(
                "умеренное преимущество агрессивных покупателей"
            )

        elif taker_ratio > 2.5:
            score -= 5
            reasons_against.append(
                "покупатели могут быть перегреты"
            )

        elif taker_ratio < 0.75:
            score -= 8
            reasons_against.append(
                "агрессивные продавцы доминируют"
            )

    # ------------------------------------------
    # Диапазон после листинга
    # ------------------------------------------

    if compression is not None:
        if compression <= 5:
            score += 10
            reasons_for.append(
                "сформирован компактный диапазон"
            )

        elif compression >= 20:
            score -= 10
            reasons_against.append(
                "волатильность остаётся хаотичной"
            )

    # ------------------------------------------
    # Open Interest
    # ------------------------------------------

    if oi_change_4h is not None:
        if 1 <= oi_change_4h <= 12:
            score += 8
            reasons_for.append(
                "OI растёт без экстремального перегрева"
            )

        elif oi_change_4h > 25:
            score -= 12
            reasons_against.append(
                "OI растёт слишком быстро"
            )

        elif oi_change_4h < -15:
            score -= 8
            reasons_against.append(
                "OI резко снижается"
            )

    # ------------------------------------------
    # Funding
    # funding приходит как десятичная ставка:
    # 0.0001 = 0.01%
    # ------------------------------------------

    if funding is not None:
        funding_percent = funding * 100

        if -0.01 <= funding_percent <= 0.02:
            score += 6
            reasons_for.append(
                "funding нейтральный"
            )

        elif funding_percent > 0.08:
            score -= 15
            reasons_against.append(
                "лонги перегреты по funding"
            )

        elif funding_percent < -0.05:
            score -= 5
            reasons_against.append(
                "экстремально отрицательный funding"
            )

    score = max(0, min(100, round(score)))

    if hard_reject:
        action = "REJECT"
        action_label = "🔴 Не покупать"

    elif score >= 75:
        action = "READY_FOR_TECHNICAL_REVIEW"
        action_label = "🟢 Рассматривать технический вход"

    elif score >= 60:
        action = "WAIT_FOR_SETUP"
        action_label = "🟡 Ждать формирования сетапа"

    else:
        action = "SKIP"
        action_label = "🔴 Пропустить"

    return {
        "available": True,
        "score": score,
        "action": action,
        "actionLabel": action_label,
        "hardReject": hard_reject,
        "metrics": {
            "firstPrice": first_price,
            "currentPrice": current_price,
            "listingHigh": listing_high,
            "listingLow": listing_low,
            "changeFromStartPercent": (
                round(change_from_start, 2)
                if change_from_start is not None
                else None
            ),
            "drawdownFromHighPercent": (
                round(drawdown_from_high, 2)
                if drawdown_from_high is not None
                else None
            ),
            "riseFromLowPercent": (
                round(rise_from_low, 2)
                if rise_from_low is not None
                else None
            ),
            "vwap": (
                round(vwap, 8)
                if vwap is not None
                else None
            ),
            "distanceFromVwapPercent": (
                round(distance_from_vwap, 2)
                if distance_from_vwap is not None
                else None
            ),
            "relativeVolume": (
                round(relative_volume, 2)
                if relative_volume is not None
                else None
            ),
            "takerRatio": (
                round(taker_ratio, 2)
                if taker_ratio is not None
                else None
            ),
            "rangeCompressionPercent": (
                round(compression, 2)
                if compression is not None
                else None
            ),
            "oiChange1hPercent": (
                round(oi_change_1h, 2)
                if oi_change_1h is not None
                else None
            ),
            "oiChange4hPercent": (
                round(oi_change_4h, 2)
                if oi_change_4h is not None
                else None
            ),
            "fundingPercent": (
                round(funding * 100, 4)
                if funding is not None
                else None
            ),
        },
        "reasonsFor": reasons_for[:8],
        "reasonsAgainst": reasons_against[:8],
    }