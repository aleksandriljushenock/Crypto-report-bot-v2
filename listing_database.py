import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DATABASE_PATH = Path("data") / "listing_database.db"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


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


def initialize_database():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS listings (
                symbol TEXT PRIMARY KEY,
                base_asset TEXT NOT NULL,
                quote_asset TEXT,
                contract_type TEXT,
                exchange_status TEXT,

                onboard_timestamp INTEGER,
                onboard_iso TEXT,

                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,

                last_price REAL,
                quote_volume_24h REAL,
                price_change_24h REAL,

                research_status TEXT NOT NULL DEFAULT 'PENDING',
                research_attempts INTEGER NOT NULL DEFAULT 0,
                last_researched_at TEXT,

                interesting INTEGER NOT NULL DEFAULT 0,
                opportunity_score REAL,
                overall_score REAL,
                data_quality REAL,

                analysis_json TEXT,
                last_error TEXT
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_listings_onboard
            ON listings(onboard_timestamp DESC)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_listings_research
            ON listings(research_status, onboard_timestamp DESC)
            """
        )


def upsert_listing(listing):
    now = utc_now()

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO listings (
                symbol,
                base_asset,
                quote_asset,
                contract_type,
                exchange_status,
                onboard_timestamp,
                onboard_iso,
                first_seen_at,
                last_seen_at,
                last_price,
                quote_volume_24h,
                price_change_24h
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(symbol) DO UPDATE SET
                base_asset = excluded.base_asset,
                quote_asset = excluded.quote_asset,
                contract_type = excluded.contract_type,
                exchange_status = excluded.exchange_status,
                onboard_timestamp = COALESCE(
                    excluded.onboard_timestamp,
                    listings.onboard_timestamp
                ),
                onboard_iso = COALESCE(
                    excluded.onboard_iso,
                    listings.onboard_iso
                ),
                last_seen_at = excluded.last_seen_at,
                last_price = excluded.last_price,
                quote_volume_24h = excluded.quote_volume_24h,
                price_change_24h = excluded.price_change_24h
            """,
            (
                listing.get("symbol"),
                listing.get("baseAsset"),
                listing.get("quoteAsset"),
                listing.get("contractType"),
                listing.get("status"),
                listing.get("onboardTimestamp"),
                listing.get("onboardIso"),
                now,
                now,
                listing.get("lastPrice"),
                listing.get("quoteVolume24h"),
                listing.get("priceChange24h"),
            ),
        )


def get_pending_listings(limit=100):
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM listings
            WHERE exchange_status = 'TRADING'
              AND research_status IN (
                  'PENDING',
                  'RETRY'
              )
            ORDER BY
                COALESCE(onboard_timestamp, 0) DESC,
                first_seen_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


def mark_research_started(symbol):
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE listings
            SET
                research_status = 'PROCESSING',
                research_attempts = research_attempts + 1,
                last_error = NULL
            WHERE symbol = ?
            """,
            (symbol,),
        )


def save_research_result(symbol, result):
    research = result.get("research", {})

    interesting = bool(
        result.get("interesting")
    )

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE listings
            SET
                research_status = 'DONE',
                last_researched_at = ?,
                interesting = ?,
                opportunity_score = ?,
                overall_score = ?,
                data_quality = ?,
                analysis_json = ?,
                last_error = NULL
            WHERE symbol = ?
            """,
            (
                utc_now(),
                1 if interesting else 0,
                result.get("opportunityScore"),
                research.get("overallScore"),
                research.get("dataQuality"),
                json.dumps(
                    result,
                    ensure_ascii=False,
                ),
                symbol,
            ),
        )


def save_research_error(symbol, error):
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE listings
            SET
                research_status = CASE
                    WHEN research_attempts >= 3
                    THEN 'FAILED'
                    ELSE 'RETRY'
                END,
                last_researched_at = ?,
                last_error = ?
            WHERE symbol = ?
            """,
            (
                utc_now(),
                str(error)[:1000],
                symbol,
            ),
        )


def reset_stuck_processing():
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE listings
            SET research_status = 'RETRY'
            WHERE research_status = 'PROCESSING'
            """
        )


def get_interesting_listings(limit=20):
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM listings
            WHERE interesting = 1
              AND research_status = 'DONE'
            ORDER BY
                opportunity_score DESC,
                onboard_timestamp DESC
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


def get_database_stats():
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(
                    CASE
                        WHEN research_status = 'DONE'
                        THEN 1 ELSE 0
                    END
                ) AS researched,
                SUM(
                    CASE
                        WHEN research_status IN (
                            'PENDING',
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
                        WHEN research_status = 'FAILED'
                        THEN 1 ELSE 0
                    END
                ) AS failed
            FROM listings
            """
        ).fetchone()

    return dict(row)