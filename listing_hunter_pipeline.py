from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)
from datetime import datetime, timezone

from listing_announcement_collector import (
    collect_listing_announcements,
)
from listing_announcement_database import (
    get_announcement_stats,
    get_interesting_announcements,
    get_pending_announcements,
    initialize_announcement_database,
    mark_processing,
    reset_processing,
    save_analysis,
    save_analysis_error,
    save_announcement,
)
from prelisting_score import (
    calculate_prelisting_score,
)
from research_analyzer import (
    analyze_project_research,
)
from research_collector import (
    collect_project_research,
)
from token_security_analyzer import (
    analyze_token_security,
)
from token_security_collector import (
    collect_token_security,
)


MAX_ANALYSIS_PER_RUN = 20
MAX_WORKERS = 2


def empty_security(message):
    return {
        "available": False,
        "score": None,
        "riskLevel": "UNKNOWN",
        "hardReject": False,
        "positives": [],
        "risks": [],
        "warnings": [message],
    }


def analyze_announcement(row):
    announcement_id = row["id"]
    symbol = row["symbol"]
    project_name = row.get(
        "project_name"
    )

    mark_processing(
        announcement_id
    )

    try:
        raw_research = (
            collect_project_research(
                symbol,
                project_name=project_name,
            )
        )

        research = analyze_project_research(
            raw_research
        )

        security = empty_security(
            "Адрес контракта не найден"
        )

        coingecko = raw_research.get(
            "coingecko",
            {},
        )

        if coingecko:
            try:
                security_raw = (
                    collect_token_security(
                        coingecko
                    )
                )

                security = (
                    analyze_token_security(
                        security_raw
                    )
                )

            except Exception as exc:
                security = empty_security(
                    f"Security error: {exc}"
                )

        announcement = {
            "source": row.get("source"),
            "title": row.get("title"),
            "url": row.get("url"),
            "symbol": symbol,
            "projectName": project_name,
            "listingAt": row.get(
                "listing_at"
            ),
        }

        score = calculate_prelisting_score(
            research=research,
            security=security,
            announcement=announcement,
        )

        result = {
            "announcement": announcement,
            "research": research,
            "security": security,
            "prelisting": score,

            "researchScore": (
                research.get(
                    "overallScore",
                    0,
                )
            ),

            "securityScore": (
                security.get("score")
            ),

            "prelistingScore": (
                score.get(
                    "prelistingScore",
                    0,
                )
            ),

            "interesting": (
                score.get(
                    "interesting",
                    False,
                )
            ),
        }

        save_analysis(
            announcement_id,
            result,
        )

        return result

    except Exception as exc:
        save_analysis_error(
            announcement_id,
            exc,
        )

        return {
            "announcement": {
                "source": row.get(
                    "source"
                ),
                "title": row.get(
                    "title"
                ),
                "url": row.get(
                    "url"
                ),
                "symbol": symbol,
                "projectName": (
                    project_name
                ),
            },
            "interesting": False,
            "error": str(exc),
        }


def run_listing_hunter(
    analysis_limit=MAX_ANALYSIS_PER_RUN,
):
    initialize_announcement_database()
    reset_processing()

    collection = (
        collect_listing_announcements()
    )

    announcements = collection.get(
        "announcements",
        [],
    )

    for item in announcements:
        save_announcement(item)

    pending = get_pending_announcements(
        limit=analysis_limit,
    )

    analyzed_now = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:
        future_map = {
            executor.submit(
                analyze_announcement,
                row,
            ): row
            for row in pending
        }

        for future in as_completed(
            future_map
        ):
            try:
                analyzed_now.append(
                    future.result()
                )
            except Exception:
                pass

    interesting_rows = (
        get_interesting_announcements(
            limit=10
        )
    )

    interesting = [
        item.get("analysis", {})
        for item in interesting_rows
        if item.get("analysis")
    ]

    interesting.sort(
        key=lambda item: item.get(
            "prelistingScore",
            0,
        ),
        reverse=True,
    )

    return {
        "runAtUtc": datetime.now(
            timezone.utc
        ).isoformat(),

        "foundNow": len(
            announcements
        ),

        "analyzedNow": len(
            analyzed_now
        ),

        "interesting": interesting,

        "interestingCount": len(
            interesting
        ),

        "sourceErrors": collection.get(
            "errors",
            [],
        ),

        "stats": (
            get_announcement_stats()
        ),
    }