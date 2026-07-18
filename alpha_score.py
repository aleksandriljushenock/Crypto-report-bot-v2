from datetime import datetime, timezone


def safe_float(value, default=None):
    try:
        if value is None:
            return default

        return float(value)

    except (TypeError, ValueError):
        return default


def clamp(value, minimum=0, maximum=100):
    return max(minimum, min(maximum, value))


def days_since_timestamp(timestamp_ms):
    if not timestamp_ms:
        return None

    try:
        listed_at = datetime.fromtimestamp(
            int(timestamp_ms) / 1000,
            tz=timezone.utc,
        )

        now = datetime.now(timezone.utc)

        return max(
            0,
            (now - listed_at).days,
        )

    except (TypeError, ValueError, OSError):
        return None


def normalize_score(value):
    value = safe_float(value, 0)

    return clamp(value) / 100


def calculate_listing_age_score(
    onboard_timestamp,
):
    days = days_since_timestamp(
        onboard_timestamp
    )

    if days is None:
        return {
            "score": 1,
            "days": None,
            "comment": "Дата листинга неизвестна",
        }

    if days <= 7:
        return {
            "score": 2,
            "days": days,
            "comment": (
                "Очень новый листинг: "
                "максимальная неопределённость"
            ),
        }

    if days <= 30:
        return {
            "score": 5,
            "days": days,
            "comment": (
                "Новый листинг с ранней историей"
            ),
        }

    if days <= 90:
        return {
            "score": 4,
            "days": days,
            "comment": (
                "Листинг достаточно новый, "
                "но уже есть история"
            ),
        }

    if days <= 365:
        return {
            "score": 3,
            "days": days,
            "comment": "Листинг моложе года",
        }

    return {
        "score": 1,
        "days": days,
        "comment": "Монета уже не является новой",
    }


def calculate_liquidity_score(listing):
    volume = safe_float(
        listing.get("quoteVolume24h"),
        0,
    )

    change_24h = safe_float(
        listing.get("priceChange24h"),
        0,
    )

    score = 0
    positives = []
    risks = []

    if volume >= 500_000_000:
        score += 8
        positives.append(
            "Очень высокая ликвидность Binance"
        )

    elif volume >= 100_000_000:
        score += 7
        positives.append(
            "Высокая ликвидность Binance"
        )

    elif volume >= 30_000_000:
        score += 5
        positives.append(
            "Приемлемая ликвидность"
        )

    elif volume >= 10_000_000:
        score += 3

    else:
        risks.append(
            "Объём Binance ниже 10M USDT"
        )

    if -15 <= change_24h <= 20:
        score += 2

    elif change_24h > 60:
        score -= 4
        risks.append(
            "Монета уже выросла более чем "
            "на 60% за сутки"
        )

    elif change_24h > 30:
        score -= 2
        risks.append(
            "Высокий риск покупки после пампа"
        )

    elif change_24h < -30:
        score -= 2
        risks.append(
            "Сильное падение за последние сутки"
        )

    return {
        "score": max(0, min(10, score)),
        "volume24h": volume,
        "priceChange24h": change_24h,
        "positives": positives,
        "risks": risks,
    }


def calculate_catalyst_score(
    research,
):
    positives = research.get(
        "positives",
        [],
    )

    warnings = research.get(
        "warnings",
        [],
    )

    combined_text = " ".join(
        str(item).lower()
        for item in positives + warnings
    )

    score = 0
    catalysts = []

    keywords = {
        "mainnet": "Mainnet",
        "testnet": "Testnet",
        "staking": "Staking",
        "integration": "Интеграция",
        "partnership": "Партнёрство",
        "grant": "Грант",
        "launch": "Запуск продукта",
        "airdrop": "Airdrop",
        "burn": "Token burn",
        "buyback": "Buyback",
    }

    for keyword, label in keywords.items():
        if keyword in combined_text:
            score += 1
            catalysts.append(label)

    return {
        "score": min(5, score),
        "catalysts": catalysts[:5],
        "comment": (
            "Автоматический поиск катализаторов "
            "пока ограничен доступными данными"
        ),
    }


def determine_alpha_grade(score):
    if score >= 85:
        return "A"

    if score >= 75:
        return "B"

    if score >= 65:
        return "C"

    if score >= 50:
        return "D"

    return "F"


def determine_alpha_action(
    score,
    hard_reject,
    data_quality,
    short_term_action,
    long_term_action,
):
    if hard_reject:
        return {
            "action": "REJECT",
            "label": "🔴 НЕ ПОКУПАТЬ",
        }

    if data_quality < 50:
        return {
            "action": "INSUFFICIENT_DATA",
            "label": (
                "⚪ НЕДОСТАТОЧНО ДАННЫХ"
            ),
        }

    if (
        score >= 80
        and long_term_action
        == "INTERESTING"
    ):
        return {
            "action": "HIGH_PRIORITY_WATCH",
            "label": (
                "🟢 ВЫСОКИЙ ПРИОРИТЕТ"
            ),
        }

    if (
        score >= 70
        and short_term_action
        in [
            "WATCH_FOR_ENTRY",
            "WAIT",
        ]
    ):
        return {
            "action": "WATCH_FOR_ENTRY",
            "label": (
                "🟡 ЖДАТЬ ТОЧКУ ВХОДА"
            ),
        }

    if score >= 60:
        return {
            "action": "RESEARCH_MORE",
            "label": (
                "🟡 ДОПОЛНИТЕЛЬНО ИЗУЧИТЬ"
            ),
        }

    return {
        "action": "SKIP",
        "label": "🔴 ПРОПУСТИТЬ",
    }


def calculate_alpha_score(
    listing,
    research,
    security,
):
    if not research.get("available"):
        return {
            "available": False,
            "alphaScore": 0,
            "grade": "F",
            "action": "INSUFFICIENT_DATA",
            "actionLabel": (
                "⚪ НЕДОСТАТОЧНО ДАННЫХ"
            ),
            "hardReject": False,
            "error": research.get(
                "error",
                "Research data недоступны",
            ),
        }

    scores = research.get("scores", {})
    data_quality = safe_float(
        research.get("dataQuality"),
        0,
    )

    tokenomics_score = safe_float(
        scores.get("tokenomics"),
        0,
    )

    market_score = safe_float(
        scores.get("market"),
        0,
    )

    development_score = safe_float(
        scores.get("development"),
        0,
    )

    adoption_score = safe_float(
        scores.get("adoption"),
        0,
    )

    overall_score = safe_float(
        research.get("overallScore"),
        0,
    )

    security_score = safe_float(
        security.get("score"),
    )

    hard_reject = bool(
        security.get("hardReject")
    )

    components = {}

    components["fundamental"] = round(
        normalize_score(overall_score) * 20,
        2,
    )

    components["tokenomics"] = round(
        normalize_score(tokenomics_score)
        * 20,
        2,
    )

    components["development"] = round(
        normalize_score(development_score)
        * 10,
        2,
    )

    components["adoption"] = round(
        normalize_score(adoption_score)
        * 10,
        2,
    )

    components["market"] = round(
        normalize_score(market_score)
        * 5,
        2,
    )

    if security_score is None:
        components["security"] = 3
    else:
        components["security"] = round(
            normalize_score(security_score)
            * 15,
            2,
        )

    liquidity = calculate_liquidity_score(
        listing
    )

    components["liquidity"] = (
        liquidity["score"]
    )

    listing_age = calculate_listing_age_score(
        listing.get("onboardTimestamp")
    )

    components["listingAge"] = (
        listing_age["score"]
    )

    catalysts = calculate_catalyst_score(
        research
    )

    components["catalysts"] = (
        catalysts["score"]
    )

    components["dataQuality"] = round(
        normalize_score(data_quality) * 5,
        2,
    )

    alpha_score = round(
        sum(components.values())
    )

    alpha_score = clamp(alpha_score)

    reasons_for = list(
        research.get("positives", [])
    )

    reasons_against = list(
        research.get("redFlags", [])
    )

    reasons_for.extend(
        liquidity.get("positives", [])
    )

    reasons_against.extend(
        liquidity.get("risks", [])
    )

    reasons_for.extend(
        security.get("positives", [])
    )

    reasons_against.extend(
        security.get("risks", [])
    )

    if catalysts.get("catalysts"):
        reasons_for.append(
            "Обнаружены потенциальные "
            "катализаторы"
        )

    if security_score is None:
        reasons_against.append(
            "Безопасность контракта "
            "не подтверждена"
        )

    metrics = research.get("metrics", {})

    fdv_to_market_cap = safe_float(
        metrics.get("fdvToMarketCap")
    )

    circulating_ratio = safe_float(
        metrics.get("circulatingRatio")
    )

    if (
        fdv_to_market_cap is not None
        and fdv_to_market_cap >= 5
    ):
        hard_reject = True
        reasons_against.insert(
            0,
            "FDV превышает Market Cap "
            "в 5 и более раз",
        )

    if (
        circulating_ratio is not None
        and circulating_ratio < 0.10
    ):
        hard_reject = True
        reasons_against.insert(
            0,
            "В обращении менее 10% "
            "предложения",
        )

    if data_quality < 35:
        hard_reject = True
        reasons_against.insert(
            0,
            "Критически низкое качество данных",
        )

    if len(reasons_against) >= 7:
        alpha_score = max(
            0,
            alpha_score - 10,
        )

    if hard_reject:
        alpha_score = min(
            alpha_score,
            49,
        )

    grade = determine_alpha_grade(
        alpha_score
    )

    action_info = determine_alpha_action(
        score=alpha_score,
        hard_reject=hard_reject,
        data_quality=data_quality,
        short_term_action=research.get(
            "shortTermAction"
        ),
        long_term_action=research.get(
            "longTermAction"
        ),
    )

    confidence = round(
        min(
            95,
            data_quality * 0.7
            + (
                20
                if security_score is not None
                else 0
            ),
        )
    )

    return {
        "available": True,
        "alphaScore": alpha_score,
        "grade": grade,
        "confidence": confidence,
        "action": action_info["action"],
        "actionLabel": action_info["label"],
        "hardReject": hard_reject,
        "components": components,
        "listingAge": listing_age,
        "liquidity": liquidity,
        "catalysts": catalysts,
        "reasonsFor": reasons_for[:10],
        "reasonsAgainst": (
            reasons_against[:10]
        ),
        "dataQuality": data_quality,
    }