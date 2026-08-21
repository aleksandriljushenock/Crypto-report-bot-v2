import json
import sqlite3
from core.sqlite_utils import connect as safe_sqlite_connect
from datetime import datetime, timedelta, timezone
from pathlib import Path


DATABASE_PATH = Path("data") / "listing_database.db"


def utc_now():
    return datetime.now(timezone.utc)


def utc_now_iso():
    return utc_now().isoformat()


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


def initialize_cache():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS listing_analysis_cache (
                symbol TEXT PRIMARY KEY,

                research_json TEXT,
                research_updated_at TEXT,
                research_error TEXT,

                security_json TEXT,
                security_updated_at TEXT,
                security_error TEXT,

                latest_result_json TEXT,
                latest_result_updated_at TEXT
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_listing_cache_research_updated
            ON listing_analysis_cache(research_updated_at)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_listing_cache_security_updated
            ON listing_analysis_cache(security_updated_at)
            """
        )


def parse_datetime(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return None


def is_fresh(updated_at, ttl_hours):
    parsed = parse_datetime(updated_at)

    if parsed is None:
        return False

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    expires_at = parsed + timedelta(
        hours=ttl_hours
    )

    return utc_now() < expires_at


def load_json(value):
    if not value:
        return None

    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


def dump_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
    )


def ensure_symbol(symbol):
    initialize_cache()

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO listing_analysis_cache (
                symbol
            )
            VALUES (?)
            ON CONFLICT(symbol) DO NOTHING
            """,
            (symbol,),
        )


def get_cache_row(symbol):
    initialize_cache()

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM listing_analysis_cache
            WHERE symbol = ?
            """,
            (symbol,),
        ).fetchone()

    if row is None:
        return None

    return dict(row)


def get_cached_research(
    symbol,
    ttl_hours=24,
):
    row = get_cache_row(symbol)

    if not row:
        return None

    if not is_fresh(
        row.get("research_updated_at"),
        ttl_hours,
    ):
        return None

    return load_json(
        row.get("research_json")
    )


def save_cached_research(
    symbol,
    research,
):
    ensure_symbol(symbol)

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE listing_analysis_cache
            SET
                research_json = ?,
                research_updated_at = ?,
                research_error = NULL
            WHERE symbol = ?
            """,
            (
                dump_json(research),
                utc_now_iso(),
                symbol,
            ),
        )


def save_research_error(
    symbol,
    error,
):
    ensure_symbol(symbol)

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE listing_analysis_cache
            SET
                research_error = ?
            WHERE symbol = ?
            """,
            (
                str(error)[:1000],
                symbol,
            ),
        )


def get_cached_security(
    symbol,
    ttl_hours=24 * 7,
):
    row = get_cache_row(symbol)

    if not row:
        return None

    if not is_fresh(
        row.get("security_updated_at"),
        ttl_hours,
    ):
        return None

    return load_json(
        row.get("security_json")
    )


def save_cached_security(
    symbol,
    security,
):
    ensure_symbol(symbol)

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE listing_analysis_cache
            SET
                security_json = ?,
                security_updated_at = ?,
                security_error = NULL
            WHERE symbol = ?
            """,
            (
                dump_json(security),
                utc_now_iso(),
                symbol,
            ),
        )


def save_security_error(
    symbol,
    error,
):
    ensure_symbol(symbol)

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE listing_analysis_cache
            SET
                security_error = ?
            WHERE symbol = ?
            """,
            (
                str(error)[:1000],
                symbol,
            ),
        )


def save_latest_result(
    symbol,
    result,
):
    ensure_symbol(symbol)

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE listing_analysis_cache
            SET
                latest_result_json = ?,
                latest_result_updated_at = ?
            WHERE symbol = ?
            """,
            (
                dump_json(result),
                utc_now_iso(),
                symbol,
            ),
        )


def get_latest_result(symbol):
    row = get_cache_row(symbol)

    if not row:
        return None

    return load_json(
        row.get("latest_result_json")
    )


def get_cache_stats():
    initialize_cache()

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS total,

                SUM(
                    CASE
                        WHEN research_json IS NOT NULL
                        THEN 1 ELSE 0
                    END
                ) AS research_cached,

                SUM(
                    CASE
                        WHEN security_json IS NOT NULL
                        THEN 1 ELSE 0
                    END
                ) AS security_cached,

                SUM(
                    CASE
                        WHEN latest_result_json IS NOT NULL
                        THEN 1 ELSE 0
                    END
                ) AS results_cached
            FROM listing_analysis_cache
            """
        ).fetchone()

    return dict(row)