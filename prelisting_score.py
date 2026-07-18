def safe_float(value, default=0.0):
    try:
        if value is None:
            return default

        return float(value)

    except (TypeError, ValueError):
        return default


def clamp(value, minimum=0, maximum=100):
    return max(
        minimum,
        min(maximum, value),
    )


def calculate_prelisting_score(
    research,
    security,
    announcement,
):
    if not research.get("available"):
        return {
            "available": False,
            "prelistingScore": 0,
            "grade": "F",
            "interesting": False,
            "action": "INSUFFICIENT_DATA",
            "actionLabel": (
                "⚪ НЕДОСТАТОЧНО ДАННЫХ"
            ),
            "reasonsFor": [],
            "reasonsAgainst": [
                research.get(
                    "error",
                    "Research недоступен",
                )
            ],
        }

    scores = research.get(
        "scores",
        {},
    )

    overall = safe_float(
        research.get("overallScore")
    )

    tokenomics = safe_float(
        scores.get("tokenomics")
    )

    development = safe_float(
        scores.get("development")
    )

    adoption = safe_float(
        scores.get("adoption")
    )

    data_quality = safe_float(
        research.get("dataQuality")
    )

    security_score = security.get(
        "score"
    )

    if security_score is None:
        security_component = 3
    else:
        security_component = (
            safe_float(security_score)
            / 100
            * 15
        )

    components = {
        "fundamental": (
            overall / 100 * 25
        ),
        "tokenomics": (
            tokenomics / 100 * 20
        ),
        "development": (
            development / 100 * 15
        ),
        "adoption": (
            adoption / 100 * 15
        ),
        "security": security_component,
        "dataQuality": (
            data_quality / 100 * 10
        ),
    }

    score = round(
        sum(components.values())
    )

    reasons_for = list(
        research.get("positives", [])
    )

    reasons_against = list(
        research.get("redFlags", [])
    )

    reasons_for.extend(
        security.get("positives", [])
    )

    reasons_against.extend(
        security.get("risks", [])
    )

    hard_reject = bool(
        security.get("hardReject")
    )

    metrics = research.get(
        "metrics",
        {},
    )

    fdv_ratio = metrics.get(
        "fdvToMarketCap"
    )

    circulating_ratio = metrics.get(
        "circulatingRatio"
    )

    if (
        fdv_ratio is not None
        and safe_float(fdv_ratio) >= 5
    ):
        hard_reject = True
        reasons_against.insert(
            0,
            "FDV/Market Cap не меньше 5",
        )

    if (
        circulating_ratio is not None
        and safe_float(
            circulating_ratio
        ) < 0.10
    ):
        hard_reject = True
        reasons_against.insert(
            0,
            "В обращении меньше 10% токенов",
        )

    if data_quality < 45:
        reasons_against.insert(
            0,
            "Низкое качество данных",
        )

    if security_score is None:
        reasons_against.append(
            "Security не подтверждён"
        )

    if hard_reject:
        score = min(score, 49)

    score = clamp(score)

    if score >= 85:
        grade = "A"
    elif score >= 75:
        grade = "B"
    elif score >= 65:
        grade = "C"
    elif score >= 50:
        grade = "D"
    else:
        grade = "F"

    if hard_reject:
        action = "REJECT"
        action_label = "🔴 НЕ ПОКУПАТЬ"

    elif score >= 80:
        action = "HIGH_PRIORITY"
        action_label = (
            "🟢 ВЫСОКИЙ ПРИОРИТЕТ"
        )

    elif score >= 68:
        action = "WATCH"
        action_label = (
            "🟡 ИЗУЧАТЬ ПЕРЕД ЛИСТИНГОМ"
        )

    else:
        action = "SKIP"
        action_label = "🔴 ПРОПУСТИТЬ"

    interesting = (
        action
        in {
            "HIGH_PRIORITY",
            "WATCH",
        }
        and data_quality >= 45
        and not hard_reject
    )

    return {
        "available": True,
        "prelistingScore": score,
        "grade": grade,
        "interesting": interesting,
        "hardReject": hard_reject,
        "action": action,
        "actionLabel": action_label,
        "components": {
            key: round(value, 2)
            for key, value
            in components.items()
        },
        "reasonsFor": reasons_for[:10],
        "reasonsAgainst": (
            reasons_against[:10]
        ),
        "source": announcement.get(
            "source"
        ),
    }