from datetime import datetime


def format_money(value):
    if value is None:
        return "N/A"

    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"

    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"

    return f"${value:,.0f}"


def format_listing_date(timestamp_ms):
    if not timestamp_ms:
        return "N/A"

    try:
        return datetime.fromtimestamp(
            timestamp_ms / 1000
        ).strftime("%d.%m.%Y")
    except Exception:
        return "N/A"


def build_new_listings_report(scan_result):
    interesting = scan_result.get(
        "interesting",
        [],
    )

    stats = scan_result.get(
        "databaseStats",
        {},
    )

    scanned_count = scan_result.get(
        "deepAnalyzedThisRun",
        scan_result.get("scannedCount", 0),
    )

    lines = []

    lines.append(
        "<b>🆕 АНАЛИЗ НОВЫХ ЛИСТИНГОВ</b>"
    )
    lines.append(
        f"Проверено монет: <b>{scanned_count}</b>"
    )
    lines.append(
        f"Прошли фильтр: <b>{len(interesting)}</b>"
    )
    lines.append("")

    if not interesting:
        lines.append(
            "<b>✅ Лучшее действие сейчас:</b> "
            "НЕ ПОКУПАТЬ"
        )
        lines.append("")
        lines.append(
            "Ни одна из последних 100 монет "
            "не соответствует минимальным требованиям."
        )
        lines.append("")
        lines.append(
            "Фильтры: фундаментал, токеномика, "
            "разработка, ликвидность, перегрев и качество данных."
        )

        return "\n".join(lines)

    lines.append(
        "<b>🎯 ИНТЕРЕСНЫЕ К ДАЛЬНЕЙШЕМУ ИЗУЧЕНИЮ</b>"
    )

    for index, item in enumerate(
        interesting[:7],
        start=1,
    ):
        listing = item.get("listing", {})
        research = item.get("research", {})
        security = item.get("security", {})
        alpha = item.get("alpha", {})
        launch = item.get("launchBehavior", {})
        buy_readiness = item.get(
            "buyReadiness",
            {},
        )
        cache_info = item.get(
            "cacheInfo",
            {},
        )

        scores = research.get(
            "scores",
            {},
        )

        metrics = research.get(
            "metrics",
            {},
        )

        symbol = listing.get("symbol")
        base_asset = listing.get("baseAsset")

        binance_url = (
            "https://www.binance.com/en/futures/"
            f"{symbol}"
        )

        coingecko_name = (
            research.get("name")
            or base_asset
        )

        lines.append("")
        lines.append(
            f"<b>{index}. {coingecko_name} "
            f"({symbol})</b>"
        )

        lines.append(
            f"<b>AI Alpha Score:</b> "
            f"{alpha.get('alphaScore', 0)}/100 "
            f"| Grade {alpha.get('grade', 'N/A')}"
        )

        lines.append(
            f"<b>Уверенность:</b> "
            f"{alpha.get('confidence', 0)}%"
        )

        lines.append(
            f"<b>Решение:</b> "
            f"{alpha.get('actionLabel', 'N/A')}"
        )

        lines.append("")

        lines.append(
            f"<b>Buy Readiness:</b> "
            f"{buy_readiness.get('score', 0)}/100"
        )

        lines.append(
            f"<b>Финальное решение:</b> "
            f"{buy_readiness.get('actionLabel', 'N/A')}"
        )        

        research_cache_text = (
            "из кэша"
            if cache_info.get("researchFromCache")
            else "обновлён"
        )

        security_cache_text = (
            "из кэша"
            if cache_info.get("securityFromCache")
            else "обновлён"
        )

        lines.append(
            f"<b>Данные:</b> "
            f"Research — {research_cache_text}; "
            f"Security — {security_cache_text}; "
            f"Launch — live"
        )

        components = alpha.get(
            "components",
            {},
        )

        lines.append("")
        lines.append("<b>Alpha components:</b>")

        lines.append("")
        lines.append(
            "<b>🚀 Launch Behavior</b>"
        )

        if not launch.get("available"):
            lines.append(
                "Недостаточно истории торгов."
            )
        else:
            lines.append(
                f"Launch Score: "
                f"{launch.get('score', 0)}/100"
            )

            lines.append(
                f"Статус: "
                f"{launch.get('actionLabel', 'N/A')}"
            )

            launch_metrics = launch.get(
                "metrics",
                {},
            )

            change_from_start = (
                launch_metrics.get(
                    "changeFromStartPercent"
                )
            )

            drawdown_from_high = (
                launch_metrics.get(
                    "drawdownFromHighPercent"
                )
            )

            distance_from_vwap = (
                launch_metrics.get(
                    "distanceFromVwapPercent"
                )
            )

            relative_volume = (
                launch_metrics.get(
                    "relativeVolume"
                )
            )

            taker_ratio = (
                launch_metrics.get(
                    "takerRatio"
                )
            )

            oi_change_4h = (
                launch_metrics.get(
                    "oiChange4hPercent"
                )
            )

            funding_percent = (
                launch_metrics.get(
                    "fundingPercent"
                )
            )

            if change_from_start is not None:
                lines.append(
                    f"От старта: "
                    f"{change_from_start:.2f}%"
                )

            if drawdown_from_high is not None:
                lines.append(
                    f"От максимума: "
                    f"{drawdown_from_high:.2f}%"
                )

            if distance_from_vwap is not None:
                lines.append(
                    f"До VWAP: "
                    f"{distance_from_vwap:.2f}%"
                )

            if relative_volume is not None:
                lines.append(
                    f"Relative Volume: "
                    f"{relative_volume:.2f}"
                )

            if taker_ratio is not None:
                lines.append(
                    f"Taker B/S: "
                    f"{taker_ratio:.2f}"
                )

            if oi_change_4h is not None:
                lines.append(
                    f"OI 4h: "
                    f"{oi_change_4h:.2f}%"
                )

            if funding_percent is not None:
                lines.append(
                    f"Funding: "
                    f"{funding_percent:.4f}%"
                )


            launch_reasons_for = (
                launch.get(
                    "reasonsFor",
                    [],
                )
            )

            launch_reasons_against = (
                launch.get(
                    "reasonsAgainst",
                    [],
                )
            )

            if launch_reasons_for:
                lines.append(
                    "<b>Что нравится:</b>"
                )

                for reason in (
                    launch_reasons_for[:3]
                ):
                    lines.append(
                        f"✓ {reason}"
                    )

            if launch_reasons_against:
                lines.append(
                    "<b>Что смущает:</b>"
                )

                for reason in (
                    launch_reasons_against[:3]
                ):
                    lines.append(
                        f"✕ {reason}"
                    )

        lines.append(
            f"Fundamental: "
            f"{components.get('fundamental', 0)}/20"
        )

        lines.append(
            f"Tokenomics: "
            f"{components.get('tokenomics', 0)}/20"
        )

        lines.append(
            f"Security: "
            f"{components.get('security', 0)}/15"
        )

        lines.append(
            f"Liquidity: "
            f"{components.get('liquidity', 0)}/10"
        )

        lines.append(
            f"Development: "
            f"{components.get('development', 0)}/10"
        )

        lines.append(
            f"Adoption: "
            f"{components.get('adoption', 0)}/10"
        )

        lines.append(
            f"Market: "
            f"{components.get('market', 0)}/5"
        )

        lines.append(
            f"Listing age: "
            f"{components.get('listingAge', 0)}/5"
        )

        lines.append(
            f"Catalysts: "
            f"{components.get('catalysts', 0)}/5"
        )

        lines.append(
            f"Data quality: "
            f"{components.get('dataQuality', 0)}/5"
        )

        lines.append(
            f"<b>Fundamental:</b> "
            f"{research.get('overallScore')}/100"
        )

        lines.append(
            f"<b>Tokenomics:</b> "
            f"{scores.get('tokenomics')}/100"
        )

        lines.append(
            f"<b>Development:</b> "
            f"{scores.get('development')}/100"
        )

        if security.get("available"):
            lines.append(
                f"<b>Security:</b> "
                f"{security.get('score')}/100 | "
                f"{security.get('riskLevel')}"
            )

            security_risks = security.get(
                "risks",
                [],
            )

            for risk in security_risks[:2]:
                lines.append(
                    f"⚠️ {risk}"
                )
        else:
            lines.append(
                "<b>Security:</b> N/A"
            )

        lines.append(
            f"<b>Adoption:</b> "
            f"{scores.get('adoption')}/100"
        )

        lines.append(
            f"<b>Listing:</b> "
            f"{format_listing_date(listing.get('onboardTimestamp'))}"
        )

        lines.append(
            f"<b>Volume 24h:</b> "
            f"{format_money(listing.get('quoteVolume24h'))}"
        )

        lines.append(
            f"<b>24h:</b> "
            f"{listing.get('priceChange24h', 0):.1f}%"
        )

        circulating_ratio = metrics.get(
            "circulatingRatio"
        )

        if circulating_ratio is not None:
            lines.append(
                f"<b>В обращении:</b> "
                f"{circulating_ratio * 100:.1f}%"
            )

        fdv_ratio = metrics.get(
            "fdvToMarketCap"
        )

        if fdv_ratio is not None:
            lines.append(
                f"<b>FDV/MC:</b> "
                f"{fdv_ratio:.2f}"
            )

        positives = item.get(
            "reasonsFor",
            [],
        )

        if positives:
            lines.append("<b>Плюсы:</b>")

            for reason in positives[:3]:
                lines.append(
                    f"✓ {reason}"
                )

        risks = item.get(
            "reasonsAgainst",
            [],
        )

        if risks:
            lines.append("<b>Риски:</b>")

            for reason in risks[:3]:
                lines.append(
                    f"✕ {reason}"
                )

        lines.append(
            "<b>Действие:</b> "
            "не покупать по рынку; "
            "перейти к графическому анализу и ждать сетап."
        )

        lines.append(
            f'<a href="{binance_url}">Binance</a>'
        )

        lines.append("━━━━━━━━━━━━━━━━━━━━")

    lines.append("")
    lines.append(
        "<b>Важно:</b> фундаментальный фильтр "
        "не является сигналом купить на старте."
    )
    lines.append(
        "Перед входом нужны структура, ликвидность, "
        "объем, стоп и R/R минимум 1:2."
    )

    return "\n".join(lines)