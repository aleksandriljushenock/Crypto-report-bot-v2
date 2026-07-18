import html


def safe_html(value):
    return html.escape(
        str(value or "")
    )


def build_listing_hunter_report(result):
    interesting = result.get(
        "interesting",
        [],
    )

    stats = result.get(
        "stats",
        {},
    )

    source_errors = result.get(
        "sourceErrors",
        [],
    )

    lines = []

    lines.append(
        "<b>🚨 AI LISTING HUNTER</b>"
    )
    lines.append("")

    lines.append(
        f"Найдено на страницах: "
        f"<b>{result.get('foundNow', 0)}</b>"
    )

    lines.append(
        f"Проанализировано сейчас: "
        f"<b>{result.get('analyzedNow', 0)}</b>"
    )

    lines.append(
        f"Всего в базе: "
        f"<b>{stats.get('total', 0)}</b>"
    )

    lines.append(
        f"В ожидании: "
        f"<b>{stats.get('pending', 0)}</b>"
    )

    lines.append(
        f"Интересных: "
        f"<b>{stats.get('interesting', 0)}</b>"
    )

    if source_errors:
        lines.append("")
        lines.append(
            "<b>Ошибки источников:</b>"
        )

        for error in source_errors[:3]:
            lines.append(
                f"• {safe_html(error.get('source'))}: "
                f"{safe_html(error.get('error'))}"
            )

    if not interesting:
        lines.append("")
        lines.append(
            "<b>Подходящих pre-listing "
            "проектов сейчас нет.</b>"
        )

        lines.append(
            "Это означает, что ни один проект "
            "не прошёл минимальные требования "
            "по фундаменталу, токеномике, "
            "разработке и безопасности."
        )

        return "\n".join(lines)

    lines.append("")
    lines.append(
        "<b>🎯 ИНТЕРЕСНЫЕ АНОНСЫ</b>"
    )

    for index, item in enumerate(
        interesting[:7],
        start=1,
    ):
        announcement = item.get(
            "announcement",
            {},
        )

        research = item.get(
            "research",
            {},
        )

        security = item.get(
            "security",
            {},
        )

        prelisting = item.get(
            "prelisting",
            {},
        )

        symbol = announcement.get(
            "symbol",
            "UNKNOWN",
        )

        project_name = (
            research.get("name")
            or announcement.get(
                "projectName"
            )
            or symbol
        )

        source = announcement.get(
            "source",
            "UNKNOWN",
        )

        url = announcement.get(
            "url"
        )

        lines.append("")
        lines.append(
            f"<b>{index}. "
            f"{safe_html(project_name)} "
            f"({safe_html(symbol)})</b>"
        )

        lines.append(
            f"<b>Биржа:</b> "
            f"{safe_html(source)}"
        )

        lines.append(
            f"<b>Pre-Listing Score:</b> "
            f"{prelisting.get('prelistingScore', 0)}/100 "
            f"| Grade "
            f"{safe_html(prelisting.get('grade', 'N/A'))}"
        )

        lines.append(
            f"<b>Решение:</b> "
            f"{safe_html(prelisting.get('actionLabel', 'N/A'))}"
        )

        lines.append(
            f"<b>Fundamental:</b> "
            f"{research.get('overallScore', 0)}/100"
        )

        if security.get("score") is not None:
            lines.append(
                f"<b>Security:</b> "
                f"{security.get('score')}/100 | "
                f"{safe_html(security.get('riskLevel'))}"
            )
        else:
            lines.append(
                "<b>Security:</b> N/A"
            )

        listing_at = announcement.get(
            "listingAt"
        )

        if listing_at:
            lines.append(
                f"<b>Время торгов:</b> "
                f"{safe_html(listing_at)}"
            )

        reasons_for = prelisting.get(
            "reasonsFor",
            [],
        )

        if reasons_for:
            lines.append(
                "<b>Плюсы:</b>"
            )

            for reason in reasons_for[:3]:
                lines.append(
                    f"✓ {safe_html(reason)}"
                )

        reasons_against = (
            prelisting.get(
                "reasonsAgainst",
                [],
            )
        )

        if reasons_against:
            lines.append(
                "<b>Риски:</b>"
            )

            for reason in (
                reasons_against[:3]
            ):
                lines.append(
                    f"✕ {safe_html(reason)}"
                )

        lines.append(
            "<b>Действие:</b> "
            "не покупать вслепую на первой свече; "
            "подготовить наблюдение и после старта "
            "проверить ликвидность, VWAP и откат."
        )

        if url:
            lines.append(
                f'<a href="{safe_html(url)}">'
                f'Официальный анонс</a>'
            )

        lines.append(
            "━━━━━━━━━━━━━━━━━━━━"
        )

    lines.append("")
    lines.append(
        "<b>Важно:</b> Pre-Listing Score "
        "оценивает проект, но не гарантирует "
        "успешный старт цены."
    )

    return "\n".join(lines)