def to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def percent_change(current, previous):
    if previous == 0:
        return 0.0
    return ((current - previous) / previous) * 100


def calculate_oi_analysis(symbol_data):
    history = symbol_data.get("openInterestHistory", [])

    if isinstance(history, dict) and "error" in history:
        return {
            "available": False,
            "label": "NO_DATA",
            "oiChange1h": None,
            "oiChange4h": None,
            "oiChange24h": None,
            "comment": "OI history недоступна",
        }

    if not isinstance(history, list) or len(history) < 5:
        return {
            "available": False,
            "label": "NO_DATA",
            "oiChange1h": None,
            "oiChange4h": None,
            "oiChange24h": None,
            "comment": "Недостаточно OI history",
        }

    values = [to_float(x.get("sumOpenInterestValue")) for x in history]

    current = values[-1]
    oi_1h = percent_change(current, values[-2]) if len(values) >= 2 else 0.0
    oi_4h = percent_change(current, values[-5]) if len(values) >= 5 else 0.0
    oi_24h = percent_change(current, values[0]) if len(values) >= 24 else 0.0

    if oi_4h > 5:
        label = "OI_GROWING_FAST"
        comment = "OI быстро растет"
    elif oi_4h > 1.5:
        label = "OI_GROWING"
        comment = "OI растет"
    elif oi_4h < -5:
        label = "OI_DROPPING_FAST"
        comment = "OI резко падает"
    elif oi_4h < -1.5:
        label = "OI_DROPPING"
        comment = "OI снижается"
    else:
        label = "OI_STABLE"
        comment = "OI стабильный"

    return {
        "available": True,
        "label": label,
        "oiChange1h": round(oi_1h, 2),
        "oiChange4h": round(oi_4h, 2),
        "oiChange24h": round(oi_24h, 2),
        "comment": comment,
    }

def calculate_funding_analysis(symbol_data):
    history = symbol_data.get("fundingRateHistory", [])

    if isinstance(history, dict) and "error" in history:
        return {
            "available": False,
            "label": "NO_DATA",
            "currentFunding": None,
            "avgFunding24h": None,
            "comment": "Funding history недоступна",
        }

    if not isinstance(history, list) or not history:
        return {
            "available": False,
            "label": "NO_DATA",
            "currentFunding": None,
            "avgFunding24h": None,
            "comment": "Недостаточно funding history",
        }

    values = [
        to_float(item.get("fundingRate")) * 100
        for item in history
    ]

    current = values[-1]
    average = sum(values) / len(values)

    if current > 0.05:
        label = "FUNDING_OVERHEATED_LONG"
        comment = "Funding перегрет в сторону лонгов"
    elif current < -0.03:
        label = "FUNDING_NEGATIVE"
        comment = "Funding отрицательный"
    elif -0.01 <= current <= 0.02:
        label = "FUNDING_NEUTRAL"
        comment = "Funding нейтральный"
    else:
        label = "FUNDING_NORMAL"
        comment = "Funding умеренный"

    return {
        "available": True,
        "label": label,
        "currentFunding": round(current, 4),
        "avgFunding24h": round(average, 4),
        "comment": comment,
    }