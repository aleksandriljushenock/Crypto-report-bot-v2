import re


NARRATIVE_RULES = {
    "AI": {
        "keywords": (
            "artificial intelligence",
            "machine learning",
            "ai agent",
            "ai agents",
            "decentralized ai",
            "gpu",
            "compute network",
            "inference",
        ),
        "score": 10,
    },
    "RWA": {
        "keywords": (
            "real world asset",
            "real-world asset",
            "rwa",
            "tokenized asset",
            "tokenized treasury",
            "treasury bill",
        ),
        "score": 10,
    },
    "DePIN": {
        "keywords": (
            "depin",
            "physical infrastructure",
            "wireless network",
            "decentralized infrastructure",
            "storage network",
            "compute network",
        ),
        "score": 9,
    },
    "Restaking": {
        "keywords": (
            "restaking",
            "liquid restaking",
            "actively validated service",
            "avs",
        ),
        "score": 8,
    },
    "Layer 2": {
        "keywords": (
            "layer 2",
            "layer-2",
            "rollup",
            "optimistic rollup",
            "zk rollup",
            "zero knowledge rollup",
        ),
        "score": 8,
    },
    "Infrastructure": {
        "keywords": (
            "infrastructure",
            "interoperability",
            "cross-chain",
            "oracle",
            "modular blockchain",
            "data availability",
            "developer tooling",
        ),
        "score": 8,
    },
    "Solana": {
        "keywords": (
            "solana",
            "spl token",
        ),
        "score": 7,
    },
    "Base": {
        "keywords": (
            "base ecosystem",
            "built on base",
            "base network",
        ),
        "score": 7,
    },
    "DeFi": {
        "keywords": (
            "defi",
            "decentralized finance",
            "lending",
            "dex",
            "liquidity protocol",
            "yield protocol",
            "derivatives protocol",
        ),
        "score": 7,
    },
    "Gaming": {
        "keywords": (
            "gaming",
            "gamefi",
            "web3 game",
            "play to earn",
            "gaming ecosystem",
        ),
        "score": 5,
    },
    "Meme": {
        "keywords": (
            "meme",
            "memecoin",
            "community token",
        ),
        "score": 2,
    },
}


HIGH_RISK_WORDS = (
    "anonymous team",
    "guaranteed return",
    "guaranteed profit",
    "risk free",
    "risk-free",
    "1000x",
    "next bitcoin",
    "presale",
)


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


def normalize_text(value):
    value = str(value or "").lower()

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value.strip()


def build_project_text(
    raw_research,
    research,
):
    coingecko = raw_research.get(
        "coingecko",
        {},
    )

    description = coingecko.get(
        "description",
        "",
    )

    categories = coingecko.get(
        "categories",
        [],
    )

    category_text = " ".join(
        str(item)
        for item in categories
        if item
    )

    positives = " ".join(
        str(item)
        for item in research.get(
            "positives",
            [],
        )
    )

    return normalize_text(
        " ".join(
            (
                description,
                category_text,
                positives,
            )
        )
    )


def analyze_narratives(
    raw_research,
    research,
):
    project_text = build_project_text(
        raw_research,
        research,
    )

    detected = []

    for narrative, config in (
        NARRATIVE_RULES.items()
    ):
        matched_keywords = []

        for keyword in config["keywords"]:
            if keyword in project_text:
                matched_keywords.append(
                    keyword
                )

        if not matched_keywords:
            continue

        detected.append({
            "name": narrative,
            "score": config["score"],
            "matchedKeywords": (
                matched_keywords[:5]
            ),
        })

    detected.sort(
        key=lambda item: item["score"],
        reverse=True,
    )

    if not detected:
        return {
            "score": 2,
            "primary": None,
            "detected": [],
            "positives": [],
            "risks": [
                "Сильный рыночный нарратив не определён"
            ],
        }

    primary = detected[0]

    score = primary["score"]

    if len(detected) >= 2:
        score += 2

    if len(detected) >= 3:
        score += 1

    return {
        "score": min(12, score),
        "primary": primary["name"],
        "detected": detected[:5],
        "positives": [
            (
                "Основной нарратив: "
                f"{primary['name']}"
            )
        ],
        "risks": [],
    }


def analyze_team_and_transparency(
    raw_research,
):
    coingecko = raw_research.get(
        "coingecko",
        {},
    )

    score = 0
    positives = []
    risks = []
    missing = []

    homepage = coingecko.get("homepage")
    description = coingecko.get(
        "description"
    )
    github_urls = coingecko.get(
        "githubUrls",
        [],
    )
    twitter_name = coingecko.get(
        "twitterScreenName"
    )
    blockchain_sites = coingecko.get(
        "blockchainSites",
        [],
    )

    if homepage:
        score += 2
        positives.append(
            "Есть официальный сайт"
        )
    else:
        risks.append(
            "Официальный сайт не найден"
        )

    if description and len(description) >= 200:
        score += 2
        positives.append(
            "Есть содержательное описание проекта"
        )
    else:
        missing.append(
            "Подробное описание проекта"
        )

    if github_urls:
        score += 3
        positives.append(
            "Есть публичный GitHub"
        )
    else:
        risks.append(
            "Публичный GitHub не найден"
        )

    if twitter_name:
        score += 2
        positives.append(
            "Есть официальный аккаунт X"
        )
    else:
        missing.append(
            "Официальный аккаунт X"
        )

    if blockchain_sites:
        score += 1

    return {
        "score": min(10, score),
        "positives": positives,
        "risks": risks,
        "missing": missing,
    }


def analyze_market_structure(
    research,
):
    metrics = research.get(
        "metrics",
        {},
    )

    fdv_ratio = safe_float(
        metrics.get("fdvToMarketCap"),
        None,
    )

    circulating_ratio = safe_float(
        metrics.get("circulatingRatio"),
        None,
    )

    volume_ratio = safe_float(
        metrics.get("volumeToMarketCap"),
        None,
    )

    score = 5
    positives = []
    risks = []
    hard_reject = False

    if fdv_ratio is None:
        risks.append(
            "FDV/Market Cap неизвестен"
        )

    elif fdv_ratio <= 1.5:
        score += 5
        positives.append(
            "Низкий разрыв FDV и капитализации"
        )

    elif fdv_ratio <= 3:
        score += 2

    elif fdv_ratio < 5:
        score -= 3
        risks.append(
            f"Повышенный FDV/MC: {fdv_ratio:.2f}"
        )

    else:
        score -= 8
        hard_reject = True
        risks.append(
            f"Критический FDV/MC: {fdv_ratio:.2f}"
        )

    if circulating_ratio is None:
        risks.append(
            "Доля предложения в обращении неизвестна"
        )

    elif circulating_ratio >= 0.60:
        score += 5
        positives.append(
            "Большая доля токенов уже в обращении"
        )

    elif circulating_ratio >= 0.30:
        score += 2

    elif circulating_ratio >= 0.10:
        score -= 3
        risks.append(
            "Низкая доля токенов в обращении"
        )

    else:
        score -= 8
        hard_reject = True
        risks.append(
            "В обращении менее 10% предложения"
        )

    if volume_ratio is not None:
        if volume_ratio >= 0.15:
            score += 3
            positives.append(
                "Хорошая ликвидность относительно капитализации"
            )

        elif volume_ratio < 0.03:
            score -= 3
            risks.append(
                "Низкая ликвидность относительно капитализации"
            )

    return {
        "score": clamp(
            score,
            0,
            15,
        ),
        "hardReject": hard_reject,
        "positives": positives,
        "risks": risks,
    }


def analyze_security_component(
    security,
):
    if not security.get("available"):
        return {
            "score": 2,
            "hardReject": False,
            "positives": [],
            "risks": [
                "Security-проверка недоступна"
            ],
        }

    security_score = safe_float(
        security.get("score"),
        0,
    )

    component_score = round(
        security_score
        / 100
        * 10,
        2,
    )

    hard_reject = bool(
        security.get("hardReject")
    )

    risks = list(
        security.get("risks", [])
    )

    positives = list(
        security.get("positives", [])
    )

    if security_score < 45:
        hard_reject = True

    return {
        "score": component_score,
        "hardReject": hard_reject,
        "positives": positives,
        "risks": risks,
    }


def detect_text_risks(
    raw_research,
    research,
):
    text = build_project_text(
        raw_research,
        research,
    )

    risks = []

    for keyword in HIGH_RISK_WORDS:
        if keyword in text:
            risks.append(
                f"Подозрительная формулировка: {keyword}"
            )

    return risks


def determine_grade(score):
    if score >= 85:
        return "A"

    if score >= 75:
        return "B"

    if score >= 65:
        return "C"

    if score >= 50:
        return "D"

    return "F"


def determine_action(
    score,
    hard_reject,
    data_quality,
):
    if hard_reject:
        return {
            "action": "REJECT",
            "actionLabel": (
                "🔴 НЕ ПОКУПАТЬ"
            ),
        }

    if data_quality < 45:
        return {
            "action": "INSUFFICIENT_DATA",
            "actionLabel": (
                "⚪ НЕДОСТАТОЧНО ДАННЫХ"
            ),
        }

    if score >= 82:
        return {
            "action": "HIGH_PRIORITY",
            "actionLabel": (
                "🟢 ВЫСОКИЙ ПРИОРИТЕТ"
            ),
        }

    if score >= 70:
        return {
            "action": "WATCH",
            "actionLabel": (
                "🟡 ИЗУЧАТЬ ПЕРЕД ЛИСТИНГОМ"
            ),
        }

    if score >= 58:
        return {
            "action": "RESEARCH_MORE",
            "actionLabel": (
                "🟡 НУЖНО БОЛЬШЕ ДАННЫХ"
            ),
        }

    return {
        "action": "SKIP",
        "actionLabel": (
            "🔴 ПРОПУСТИТЬ"
        ),
    }


def calculate_alpha_v2(
    raw_research,
    research,
    security,
    discovery=None,
):
    discovery = discovery or {}

    if not research.get("available"):
        return {
            "available": False,
            "score": 0,
            "grade": "F",
            "interesting": False,
            "hardReject": False,
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
        research.get("overallScore"),
        0,
    )

    tokenomics = safe_float(
        scores.get("tokenomics"),
        0,
    )

    development = safe_float(
        scores.get("development"),
        0,
    )

    adoption = safe_float(
        scores.get("adoption"),
        0,
    )

    data_quality = safe_float(
        research.get("dataQuality"),
        0,
    )

    narrative = analyze_narratives(
        raw_research,
        research,
    )

    transparency = (
        analyze_team_and_transparency(
            raw_research
        )
    )

    market_structure = (
        analyze_market_structure(
            research
        )
    )

    security_component = (
        analyze_security_component(
            security
        )
    )

    components = {
        "fundamental": round(
            overall / 100 * 18,
            2,
        ),
        "tokenomics": round(
            tokenomics / 100 * 18,
            2,
        ),
        "development": round(
            development / 100 * 12,
            2,
        ),
        "adoption": round(
            adoption / 100 * 10,
            2,
        ),
        "narrative": round(
            narrative["score"],
            2,
        ),
        "transparency": round(
            transparency["score"],
            2,
        ),
        "marketStructure": round(
            market_structure["score"],
            2,
        ),
        "security": round(
            security_component["score"],
            2,
        ),
        "dataQuality": round(
            data_quality / 100 * 5,
            2,
        ),
    }

    score = round(
        sum(components.values())
    )

    reasons_for = []

    reasons_for.extend(
        research.get("positives", [])
    )
    reasons_for.extend(
        narrative.get("positives", [])
    )
    reasons_for.extend(
        transparency.get("positives", [])
    )
    reasons_for.extend(
        market_structure.get(
            "positives",
            [],
        )
    )
    reasons_for.extend(
        security_component.get(
            "positives",
            [],
        )
    )

    reasons_against = []

    reasons_against.extend(
        research.get("redFlags", [])
    )
    reasons_against.extend(
        narrative.get("risks", [])
    )
    reasons_against.extend(
        transparency.get("risks", [])
    )
    reasons_against.extend(
        market_structure.get(
            "risks",
            [],
        )
    )
    reasons_against.extend(
        security_component.get(
            "risks",
            [],
        )
    )
    reasons_against.extend(
        detect_text_risks(
            raw_research,
            research,
        )
    )

    missing_data = [
        "VC и раунды финансирования",
        "График token unlock",
        "Рост подписчиков за период",
        "Концентрация крупнейших держателей",
        "Smart-money кошельки",
    ]

    missing_data.extend(
        transparency.get(
            "missing",
            [],
        )
    )

    hard_reject = (
        market_structure.get(
            "hardReject",
            False,
        )
        or security_component.get(
            "hardReject",
            False,
        )
    )

    if data_quality < 35:
        hard_reject = True

        reasons_against.insert(
            0,
            "Критически низкое качество данных"
        )

    if len(reasons_against) >= 7:
        score -= 7

    if security.get("score") is None:
        score -= 3

    if hard_reject:
        score = min(
            score,
            49,
        )

    score = clamp(score)

    grade = determine_grade(score)

    decision = determine_action(
        score=score,
        hard_reject=hard_reject,
        data_quality=data_quality,
    )

    interesting = (
        decision["action"]
        in {
            "HIGH_PRIORITY",
            "WATCH",
        }
        and not hard_reject
    )

    confidence = round(
        min(
            95,
            data_quality * 0.75
            + (
                15
                if security.get("score")
                is not None
                else 0
            ),
        )
    )

    return {
        "available": True,
        "score": score,
        "grade": grade,
        "confidence": confidence,
        "interesting": interesting,
        "hardReject": hard_reject,
        "action": decision["action"],
        "actionLabel": (
            decision["actionLabel"]
        ),
        "primaryNarrative": (
            narrative.get("primary")
        ),
        "narratives": (
            narrative.get("detected", [])
        ),
        "components": components,
        "reasonsFor": reasons_for[:12],
        "reasonsAgainst": (
            reasons_against[:12]
        ),
        "missingData": (
            list(dict.fromkeys(
                missing_data
            ))[:10]
        ),
        "source": discovery.get("source"),
    }