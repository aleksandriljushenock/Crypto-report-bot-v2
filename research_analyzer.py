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


def ratio(numerator, denominator):
    numerator = safe_float(numerator)
    denominator = safe_float(denominator)

    if numerator is None or denominator in (None, 0):
        return None

    return numerator / denominator


def days_since(date_value):
    if not date_value:
        return None

    try:
        parsed = datetime.fromisoformat(
            date_value.replace("Z", "+00:00")
        )

        now = datetime.now(timezone.utc)

        return (now - parsed).days

    except (TypeError, ValueError):
        return None


def analyze_tokenomics(coingecko):
    market = coingecko.get("market", {})

    market_cap = safe_float(
        market.get("marketCapUsd")
    )

    fdv = safe_float(
        market.get("fullyDilutedValuationUsd")
    )

    circulating_supply = safe_float(
        market.get("circulatingSupply")
    )

    total_supply = safe_float(
        market.get("totalSupply")
    )

    max_supply = safe_float(
        market.get("maxSupply")
    )

    score = 50
    positives = []
    risks = []
    warnings = []

    fdv_to_market_cap = ratio(
        fdv,
        market_cap,
    )

    circulating_ratio = ratio(
        circulating_supply,
        total_supply or max_supply,
    )

    if fdv_to_market_cap is None:
        warnings.append(
            "Нет данных для расчета FDV/Market Cap"
        )

    elif fdv_to_market_cap <= 1.25:
        score += 20
        positives.append(
            "FDV близок к текущей капитализации"
        )

    elif fdv_to_market_cap <= 2:
        score += 8
        positives.append(
            "Умеренный разрыв FDV и Market Cap"
        )

    elif fdv_to_market_cap <= 4:
        score -= 10
        risks.append(
            f"FDV превышает Market Cap в "
            f"{fdv_to_market_cap:.2f} раза"
        )

    else:
        score -= 25
        risks.append(
            f"Очень высокий FDV/Market Cap: "
            f"{fdv_to_market_cap:.2f}"
        )

    if circulating_ratio is None:
        warnings.append(
            "Нет данных о доле токенов в обращении"
        )

    elif circulating_ratio >= 0.75:
        score += 20
        positives.append(
            "Большая часть предложения уже в обращении"
        )

    elif circulating_ratio >= 0.5:
        score += 8
        positives.append(
            "Умеренная доля предложения в обращении"
        )

    elif circulating_ratio >= 0.25:
        score -= 10
        risks.append(
            f"В обращении только "
            f"{circulating_ratio * 100:.1f}% предложения"
        )

    else:
        score -= 25
        risks.append(
            f"Очень низкий circulating supply: "
            f"{circulating_ratio * 100:.1f}%"
        )

    if total_supply is None and max_supply is None:
        risks.append(
            "Максимальное или общее предложение неизвестно"
        )
        score -= 5

    return {
        "score": clamp(score),
        "fdvToMarketCap": (
            round(fdv_to_market_cap, 3)
            if fdv_to_market_cap is not None
            else None
        ),
        "circulatingRatio": (
            round(circulating_ratio, 3)
            if circulating_ratio is not None
            else None
        ),
        "positives": positives,
        "risks": risks,
        "warnings": warnings,
    }


def analyze_market(coingecko):
    market = coingecko.get("market", {})

    market_cap = safe_float(
        market.get("marketCapUsd")
    )

    volume = safe_float(
        market.get("volume24hUsd")
    )

    price_change_24h = safe_float(
        market.get("priceChange24hPercent")
    )

    price_change_7d = safe_float(
        market.get("priceChange7dPercent")
    )

    price_change_30d = safe_float(
        market.get("priceChange30dPercent")
    )

    ath_change = safe_float(
        market.get("athChangePercent")
    )

    score = 50
    positives = []
    risks = []
    warnings = []

    volume_to_market_cap = ratio(
        volume,
        market_cap,
    )

    if volume_to_market_cap is None:
        warnings.append(
            "Нельзя рассчитать Volume/Market Cap"
        )

    elif volume_to_market_cap >= 0.5:
        score += 15
        positives.append(
            "Очень высокая ликвидность относительно капитализации"
        )

    elif volume_to_market_cap >= 0.15:
        score += 10
        positives.append(
            "Хорошая ликвидность"
        )

    elif volume_to_market_cap >= 0.05:
        score += 2

    else:
        score -= 15
        risks.append(
            "Низкий объем относительно капитализации"
        )

    if price_change_24h is not None:
        if price_change_24h > 50:
            score -= 20
            risks.append(
                f"Цена выросла на {price_change_24h:.1f}% за 24 часа"
            )

        elif price_change_24h > 20:
            score -= 10
            risks.append(
                "Высокий риск покупки после пампа"
            )

        elif -10 <= price_change_24h <= 10:
            score += 5

    if price_change_7d is not None:
        if price_change_7d > 150:
            score -= 20
            risks.append(
                f"Рост за 7 дней слишком резкий: "
                f"{price_change_7d:.1f}%"
            )

        elif price_change_7d > 60:
            score -= 10
            risks.append(
                "Монета сильно перегрета за неделю"
            )

    if price_change_30d is not None:
        if price_change_30d > 300:
            score -= 15
            risks.append(
                "Экстремальный рост за 30 дней"
            )

    if ath_change is not None:
        distance_from_ath = abs(ath_change)

        if distance_from_ath < 5:
            score -= 8
            risks.append(
                "Цена торгуется почти у исторического максимума"
            )

        elif distance_from_ath > 70:
            score -= 5
            risks.append(
                "Монета находится далеко ниже ATH"
            )

    return {
        "score": clamp(score),
        "volumeToMarketCap": (
            round(volume_to_market_cap, 3)
            if volume_to_market_cap is not None
            else None
        ),
        "positives": positives,
        "risks": risks,
        "warnings": warnings,
    }


def analyze_development(coingecko, github):
    developer = coingecko.get(
        "developer",
        {},
    )

    repositories = [
        repo
        for repo in github
        if not repo.get("error")
    ]

    score = 30
    positives = []
    risks = []
    warnings = []

    commit_count = safe_float(
        developer.get("commitCount4Weeks"),
        0,
    )

    if commit_count >= 100:
        score += 25
        positives.append(
            "Очень высокая GitHub-активность"
        )

    elif commit_count >= 30:
        score += 18
        positives.append(
            "Активная разработка"
        )

    elif commit_count >= 5:
        score += 8

    elif commit_count == 0:
        score -= 10
        risks.append(
            "Нет зафиксированных коммитов за 4 недели"
        )

    if not repositories:
        score -= 15
        warnings.append(
            "Публичные GitHub-репозитории не найдены"
        )

        return {
            "score": clamp(score),
            "repositoryCount": 0,
            "totalStars": 0,
            "totalForks": 0,
            "recentRepositoryCount": 0,
            "positives": positives,
            "risks": risks,
            "warnings": warnings,
        }

    total_stars = sum(
        int(repo.get("stars") or 0)
        for repo in repositories
    )

    total_forks = sum(
        int(repo.get("forks") or 0)
        for repo in repositories
    )

    recent_repositories = 0
    archived_count = 0

    for repo in repositories:
        pushed_days = days_since(
            repo.get("pushedAt")
        )

        if pushed_days is not None and pushed_days <= 30:
            recent_repositories += 1

        if repo.get("archived"):
            archived_count += 1

    if recent_repositories >= 2:
        score += 20
        positives.append(
            "Несколько репозиториев обновлялись в последний месяц"
        )

    elif recent_repositories == 1:
        score += 10
        positives.append(
            "Есть недавно обновленный репозиторий"
        )

    else:
        score -= 15
        risks.append(
            "Репозитории давно не обновлялись"
        )

    if total_stars >= 5000:
        score += 15
        positives.append(
            "Высокий интерес разработчиков к проекту"
        )

    elif total_stars >= 500:
        score += 8

    elif total_stars < 20:
        score -= 5
        risks.append(
            "Очень мало GitHub stars"
        )

    if archived_count == len(repositories):
        score -= 25
        risks.append(
            "Все найденные репозитории архивированы"
        )

    elif archived_count > 0:
        score -= 5
        warnings.append(
            "Часть репозиториев архивирована"
        )

    return {
        "score": clamp(score),
        "repositoryCount": len(repositories),
        "totalStars": total_stars,
        "totalForks": total_forks,
        "recentRepositoryCount": recent_repositories,
        "positives": positives,
        "risks": risks,
        "warnings": warnings,
    }


def analyze_adoption(coingecko, defillama):
    community = coingecko.get(
        "community",
        {},
    )

    categories = coingecko.get(
        "categories",
        [],
    )

    score = 35
    positives = []
    risks = []
    warnings = []

    twitter_followers = safe_float(
        community.get("twitterFollowers"),
        0,
    )

    telegram_users = safe_float(
        community.get("telegramUsers"),
        0,
    )

    reddit_subscribers = safe_float(
        community.get("redditSubscribers"),
        0,
    )

    if twitter_followers >= 500000:
        score += 15
        positives.append(
            "Крупное сообщество в X"
        )

    elif twitter_followers >= 100000:
        score += 10

    elif twitter_followers >= 10000:
        score += 5

    if telegram_users >= 100000:
        score += 10

    elif telegram_users >= 10000:
        score += 5

    if reddit_subscribers >= 50000:
        score += 5

    if isinstance(defillama, dict) and defillama.get("tvlUsd") is not None:
        tvl = safe_float(
            defillama.get("tvlUsd"),
            0,
        )

        market_cap_to_tvl = safe_float(
            defillama.get("marketCapToTvl")
        )

        if tvl >= 1_000_000_000:
            score += 25
            positives.append(
                "TVL превышает 1 млрд USD"
            )

        elif tvl >= 100_000_000:
            score += 15
            positives.append(
                "Существенный TVL"
            )

        elif tvl >= 10_000_000:
            score += 5

        else:
            score -= 10
            risks.append(
                "Низкий TVL"
            )

        if market_cap_to_tvl is not None:
            if market_cap_to_tvl <= 1:
                score += 10
                positives.append(
                    "Market Cap/TVL выглядит умеренно"
                )

            elif market_cap_to_tvl >= 10:
                score -= 15
                risks.append(
                    f"Высокий Market Cap/TVL: "
                    f"{market_cap_to_tvl:.2f}"
                )

    else:
        defi_categories = [
            category.lower()
            for category in categories
            if category
        ]

        looks_like_defi = any(
            keyword in category
            for category in defi_categories
            for keyword in (
                "decentralized finance",
                "defi",
                "dex",
                "lending",
                "yield",
                "liquid staking",
            )
        )

        if looks_like_defi:
            score -= 10
            risks.append(
                "Проект выглядит как DeFi, но TVL не найден"
            )

        else:
            warnings.append(
                "DefiLlama не применим или проект не найден"
            )

    return {
        "score": clamp(score),
        "twitterFollowers": int(twitter_followers),
        "telegramUsers": int(telegram_users),
        "redditSubscribers": int(reddit_subscribers),
        "positives": positives,
        "risks": risks,
        "warnings": warnings,
    }


def calculate_data_quality(
    coingecko,
    defillama,
    github,
):
    available_points = 0
    total_points = 8

    if coingecko.get("description"):
        available_points += 1

    if coingecko.get("homepage"):
        available_points += 1

    if coingecko.get("market", {}).get("marketCapUsd"):
        available_points += 1

    if coingecko.get("market", {}).get(
        "fullyDilutedValuationUsd"
    ):
        available_points += 1

    if coingecko.get("market", {}).get(
        "circulatingSupply"
    ):
        available_points += 1

    if github:
        available_points += 1

    if isinstance(defillama, dict) and defillama.get("tvlUsd"):
        available_points += 1

    if coingecko.get("twitterScreenName"):
        available_points += 1

    return round(
        available_points / total_points * 100
    )


def determine_actions(
    overall_score,
    market_score,
    tokenomics_score,
    development_score,
    adoption_score,
    red_flags,
    data_quality,
):
    if data_quality < 45:
        return {
            "shortTermAction": "INSUFFICIENT_DATA",
            "longTermAction": "INSUFFICIENT_DATA",
            "comment": (
                "Недостаточно данных для надежного решения"
            ),
        }

    if len(red_flags) >= 4:
        return {
            "shortTermAction": "SKIP",
            "longTermAction": "SKIP",
            "comment": (
                "Слишком много фундаментальных рисков"
            ),
        }

    if market_score >= 70 and overall_score >= 70:
        short_term_action = "WATCH_FOR_ENTRY"

    elif market_score >= 55:
        short_term_action = "WAIT"

    else:
        short_term_action = "SKIP"

    if (
        overall_score >= 75
        and tokenomics_score >= 60
        and development_score >= 60
        and adoption_score >= 55
    ):
        long_term_action = "INTERESTING"

    elif overall_score >= 60:
        long_term_action = "WATCH"

    else:
        long_term_action = "SKIP"

    return {
        "shortTermAction": short_term_action,
        "longTermAction": long_term_action,
        "comment": (
            "Фундаментальная оценка не является сигналом "
            "для немедленной покупки"
        ),
    }


def analyze_project_research(raw_data):
    if not raw_data.get("available"):
        return {
            "available": False,
            "error": raw_data.get(
                "error",
                "Research data недоступны",
            ),
        }

    coingecko = raw_data.get("coingecko", {})
    defillama = raw_data.get("defillama")
    github = raw_data.get("github", [])

    tokenomics = analyze_tokenomics(
        coingecko
    )

    market = analyze_market(
        coingecko
    )

    development = analyze_development(
        coingecko,
        github,
    )

    adoption = analyze_adoption(
        coingecko,
        defillama,
    )

    data_quality = calculate_data_quality(
        coingecko,
        defillama,
        github,
    )

    weighted_score = (
        tokenomics["score"] * 0.30
        + market["score"] * 0.25
        + development["score"] * 0.25
        + adoption["score"] * 0.20
    )

    overall_score = round(
        weighted_score
    )

    positives = (
        tokenomics["positives"]
        + market["positives"]
        + development["positives"]
        + adoption["positives"]
    )

    red_flags = (
        tokenomics["risks"]
        + market["risks"]
        + development["risks"]
        + adoption["risks"]
    )

    warnings = (
        tokenomics["warnings"]
        + market["warnings"]
        + development["warnings"]
        + adoption["warnings"]
    )

    actions = determine_actions(
        overall_score=overall_score,
        market_score=market["score"],
        tokenomics_score=tokenomics["score"],
        development_score=development["score"],
        adoption_score=adoption["score"],
        red_flags=red_flags,
        data_quality=data_quality,
    )

    return {
        "available": True,
        "symbol": raw_data.get("requestedSymbol"),
        "name": coingecko.get("name"),
        "overallScore": overall_score,
        "dataQuality": data_quality,
        "scores": {
            "tokenomics": tokenomics["score"],
            "market": market["score"],
            "development": development["score"],
            "adoption": adoption["score"],
        },
        "metrics": {
            "fdvToMarketCap": tokenomics.get(
                "fdvToMarketCap"
            ),
            "circulatingRatio": tokenomics.get(
                "circulatingRatio"
            ),
            "volumeToMarketCap": market.get(
                "volumeToMarketCap"
            ),
            "repositoryCount": development.get(
                "repositoryCount"
            ),
            "recentRepositoryCount": development.get(
                "recentRepositoryCount"
            ),
            "totalGithubStars": development.get(
                "totalStars"
            ),
            "tvlUsd": (
                defillama.get("tvlUsd")
                if isinstance(defillama, dict)
                else None
            ),
            "marketCapToTvl": (
                defillama.get("marketCapToTvl")
                if isinstance(defillama, dict)
                else None
            ),
        },
        "positives": positives[:10],
        "redFlags": red_flags[:10],
        "warnings": warnings[:10],
        "shortTermAction": actions[
            "shortTermAction"
        ],
        "longTermAction": actions[
            "longTermAction"
        ],
        "comment": actions["comment"],
    }