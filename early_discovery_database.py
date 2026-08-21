import json
import sqlite3
from core.sqlite_utils import connect as safe_sqlite_connect
from datetime import datetime, timezone
from pathlib import Path


DATABASE_PATH = Path("data") / "early_discovery.db"


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def get_connection():
    DATABASE_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    connection = safe_sqlite_connect(
        DATABASE_PATH,
        timeout=30,
    )

    connection.row_factory = sqlite3.Row
    return connection


def initialize_database():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS discovered_projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                source TEXT NOT NULL,
                external_id TEXT NOT NULL,

                symbol TEXT,
                project_name TEXT,
                slug TEXT,

                contract_platform TEXT,
                contract_address TEXT,

                announcement_url TEXT,
                discovered_at TEXT NOT NULL,
                source_added_at TEXT,

                status TEXT NOT NULL DEFAULT 'NEW',

                analysis_json TEXT,
                prelisting_score REAL,
                interesting INTEGER NOT NULL DEFAULT 0,

                analyzed_at TEXT,
                last_error TEXT,

                UNIQUE(source, external_id)
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_discovered_status
            ON discovered_projects(
                status,
                discovered_at DESC
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_discovered_score
            ON discovered_projects(
                interesting,
                prelisting_score DESC
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS source_snapshots (
                source TEXT PRIMARY KEY,
                snapshot_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )


def load_source_snapshot(source):
    initialize_database()

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT snapshot_json
            FROM source_snapshots
            WHERE source = ?
            """,
            (source,),
        ).fetchone()

    if row is None:
        return None

    try:
        return json.loads(row["snapshot_json"])
    except (TypeError, json.JSONDecodeError):
        return None


def save_source_snapshot(source, snapshot):
    initialize_database()

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO source_snapshots (
                source,
                snapshot_json,
                updated_at
            )
            VALUES (?, ?, ?)

            ON CONFLICT(source) DO UPDATE SET
                snapshot_json = excluded.snapshot_json,
                updated_at = excluded.updated_at
            """,
            (
                source,
                json.dumps(
                    snapshot,
                    ensure_ascii=False,
                ),
                utc_now_iso(),
            ),
        )


def save_discovered_project(item):
    initialize_database()

    source = item.get("source")
    external_id = str(item.get("externalId"))

    with get_connection() as connection:
        existed = connection.execute(
            "SELECT 1 FROM discovered_projects WHERE source=? AND external_id=?",
            (source, external_id),
        ).fetchone() is not None

        connection.execute(
            """
            INSERT INTO discovered_projects (
                source, external_id, symbol, project_name, slug,
                contract_platform, contract_address, announcement_url,
                discovered_at, source_added_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, external_id) DO UPDATE SET
                symbol=COALESCE(excluded.symbol, discovered_projects.symbol),
                project_name=COALESCE(excluded.project_name, discovered_projects.project_name),
                slug=COALESCE(excluded.slug, discovered_projects.slug),
                contract_platform=COALESCE(excluded.contract_platform, discovered_projects.contract_platform),
                contract_address=COALESCE(excluded.contract_address, discovered_projects.contract_address),
                announcement_url=COALESCE(excluded.announcement_url, discovered_projects.announcement_url),
                source_added_at=COALESCE(excluded.source_added_at, discovered_projects.source_added_at)
            """,
            (
                source, external_id, item.get("symbol"), item.get("projectName"),
                item.get("slug"), item.get("contractPlatform"), item.get("contractAddress"),
                item.get("announcementUrl"), utc_now_iso(), item.get("sourceAddedAt"),
            ),
        )

    return not existed


def get_pending_projects(limit=30):
    initialize_database()

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM discovered_projects
            WHERE status IN ('NEW', 'RETRY')
              AND symbol IS NOT NULL
            ORDER BY discovered_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [dict(row) for row in rows]


def mark_processing(project_id):
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE discovered_projects
            SET
                status = 'PROCESSING',
                last_error = NULL
            WHERE id = ?
            """,
            (project_id,),
        )


def save_analysis(project_id, result):
    prelisting = result.get("prelisting", {})

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE discovered_projects
            SET
                status = 'DONE',
                analysis_json = ?,
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
                prelisting.get(
                    "prelistingScore",
                    0,
                ),
                (
                    1
                    if prelisting.get("interesting")
                    else 0
                ),
                utc_now_iso(),
                project_id,
            ),
        )


def save_analysis_error(project_id, error):
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE discovered_projects
            SET
                status = 'RETRY',
                analyzed_at = ?,
                last_error = ?
            WHERE id = ?
            """,
            (
                utc_now_iso(),
                str(error)[:1000],
                project_id,
            ),
        )


def reset_processing():
    initialize_database()

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE discovered_projects
            SET status = 'RETRY'
            WHERE status = 'PROCESSING'
            """
        )


def get_interesting_projects(limit=10):
    initialize_database()

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM discovered_projects
            WHERE status = 'DONE'
              AND interesting = 1
            ORDER BY
                prelisting_score DESC,
                discovered_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    result = []

    for row in rows:
        item = dict(row)

        try:
            item["analysis"] = json.loads(
                item.get("analysis_json") or "{}"
            )
        except json.JSONDecodeError:
            item["analysis"] = {}

        result.append(item)

    return result


def get_stats():
    initialize_database()

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
                        WHEN status IN ('NEW', 'RETRY')
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
            FROM discovered_projects
            """
        ).fetchone()

    return dict(row)

def get_top_rejected_projects(limit=10):
    initialize_database()

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM discovered_projects
            WHERE status = 'DONE'
              AND interesting = 0
              AND analysis_json IS NOT NULL
            ORDER BY
                prelisting_score DESC,
                discovered_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    result = []

    for row in rows:
        item = dict(row)

        try:
            item["analysis"] = json.loads(
                item.get("analysis_json") or "{}"
            )
        except json.JSONDecodeError:
            item["analysis"] = {}

        result.append(item)

    return result