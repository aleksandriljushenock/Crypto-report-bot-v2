def to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def kline_close_change(candles, lookback):
    if not candles or len(candles) <= lookback:
        return 0.0

    current = candles[-1]["close"]
    previous = candles[-lookback]["close"]

    if previous == 0:
        return 0.0

    return ((current - previous) / previous) * 100


def calculate_relative_strength(symbol_data, btc_data):
    symbol_klines = symbol_data.get("parsedKlines", {})
    btc_klines = btc_data.get("parsedKlines", {}) if btc_data else {}

    result = {}

    configs = {
        "15m": 4,     # примерно 1 час
        "1h": 4,      # примерно 4 часа
        "4h": 6,      # примерно 24 часа
    }

    for interval, lookback in configs.items():
        symbol_candles = symbol_klines.get(interval, [])
        btc_candles = btc_klines.get(interval, [])

        symbol_change = kline_close_change(symbol_candles, lookback)
        btc_change = kline_close_change(btc_candles, lookback)

        rs = symbol_change - btc_change

        result[interval] = {
            "symbolChange": round(symbol_change, 3),
            "btcChange": round(btc_change, 3),
            "relativeStrength": round(rs, 3),
        }

    score = 0

    if result["15m"]["relativeStrength"] > 0:
        score += 1
    if result["1h"]["relativeStrength"] > 0:
        score += 2
    if result["4h"]["relativeStrength"] > 0:
        score += 3

    if score >= 5:
        label = "STRONG"
    elif score >= 3:
        label = "NEUTRAL"
    else:
        label = "WEAK"

    return {
        "label": label,
        "score": score,
        "details": result,
    }