import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DATABASE_PATH = (
    Path("data")
    / "listing_announcements.db"
)


def utc_now_iso():
    return datetime.now(
        timezone.utc
    ).isoformat()


def get_connection():
    DATABASE_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    connection = sqlite3.connect(
        DATABASE_PATH,
        timeout=30,
    )

    connection.row_factory = sqlite3.Row

    return connection


def initialize_announcement_database():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS announcements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                source TEXT NOT NULL,
                external_key TEXT NOT NULL,

                title TEXT NOT NULL,
                url TEXT NOT NULL,

                symbol TEXT,
                project_name TEXT,

                detected_at TEXT NOT NULL,
                published_at TEXT,
                listing_at TEXT,

                status TEXT NOT NULL DEFAULT 'NEW',

                research_json TEXT,
                research_score REAL,
                security_score REAL,
                prelisting_score REAL,

                interesting INTEGER NOT NULL DEFAULT 0,
                analyzed_at TEXT,
                last_error TEXT,

                UNIQUE(source, external_key)
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_announcement_status
            ON announcements(status, detected_at DESC)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_announcement_score
            ON announcements(
                interesting,
                prelisting_score DESC
            )
            """
        )


def save_announcement(item):
    initialize_announcement_database()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO announcements (
                source,
                external_key,
                title,
                url,
                symbol,
                project_name,
                detected_at,
                published_at,
                listing_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(
                source,
                external_key
            ) DO UPDATE SET
                title = excluded.title,
                url = excluded.url,
                symbol = COALESCE(
                    excluded.symbol,
                    announcements.symbol
                ),
                project_name = COALESCE(
                    excluded.project_name,
                    announcements.project_name
                ),
                published_at = COALESCE(
                    excluded.published_at,
                    announcements.published_at
                ),
                listing_at = COALESCE(
                    excluded.listing_at,
                    announcements.listing_at
                )
            """,
            (
                item.get("source"),
                item.get("externalKey"),
                item.get("title"),
                item.get("url"),
                item.get("symbol"),
                item.get("projectName"),
                utc_now_iso(),
                item.get("publishedAt"),
                item.get("listingAt"),
            ),
        )

        return cursor.rowcount > 0


def get_pending_announcements(limit=30):
    initialize_announcement_database()

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM announcements
            WHERE status IN (
                'NEW',
                'RETRY'
            )
              AND symbol IS NOT NULL
            ORDER BY detected_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


def mark_processing(announcement_id):
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE announcements
            SET
                status = 'PROCESSING',
                last_error = NULL
            WHERE id = ?
            """,
            (announcement_id,),
        )


def save_analysis(
    announcement_id,
    result,
):
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE announcements
            SET
                status = 'DONE',
                research_json = ?,
                research_score = ?,
                security_score = ?,
                prelisting_score = ?,
                interesting = ?,
                analyzed_at = ?,
                last_error = NULL
            WHERE id = ?
            """,
            (
                json.dumps(
                    result,
                    ensure_ascii=False,
                ),
                result.get("researchScore"),
                result.get("securityScore"),
                result.get("prelistingScore"),
                (
                    1
                    if result.get("interesting")
                    else 0
                ),
                utc_now_iso(),
                announcement_id,
            ),
        )


def save_analysis_error(
    announcement_id,
    error,
):
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE announcements
            SET
                status = 'RETRY',
                analyzed_at = ?,
                last_error = ?
            WHERE id = ?
            """,
            (
                utc_now_iso(),
                str(error)[:1000],
                announcement_id,
            ),
        )


def reset_processing():
    initialize_announcement_database()

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE announcements
            SET status = 'RETRY'
            WHERE status = 'PROCESSING'
            """
        )


def get_interesting_announcements(
    limit=10,
):
    initialize_announcement_database()

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM announcements
            WHERE status = 'DONE'
              AND interesting = 1
            ORDER BY
                prelisting_score DESC,
                detected_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    result = []

    for row in rows:
        item = dict(row)

        try:
            item["analysis"] = json.loads(
                item.get("research_json")
                or "{}"
            )
        except json.JSONDecodeError:
            item["analysis"] = {}

        result.append(item)

    return result


def get_announcement_stats():
    initialize_announcement_database()

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS total,

                SUM(
                    CASE
                        WHEN status = 'DONE'
                        THEN 1 ELSE 0
                    END
                ) AS analyzed,

                SUM(
                    CASE
                        WHEN status IN (
                            'NEW',
                            'RETRY'
                        )
                        THEN 1 ELSE 0
                    END
                ) AS pending,

                SUM(
                    CASE
                        WHEN interesting = 1
                        THEN 1 ELSE 0
                    END
                ) AS interesting,

                SUM(
                    CASE
                        WHEN last_error IS NOT NULL
                        THEN 1 ELSE 0
                    END
                ) AS errors
            FROM announcements
            """
        ).fetchone()

    return dict(row)