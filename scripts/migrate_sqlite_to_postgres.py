#!/usr/bin/env python3
"""One-time SQLite -> Postgres data migration utility."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine


def _load_settings():
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from src.config import settings

    return settings

TABLES = ("users", "sources", "events", "jobs")


def _sqlite_path_from_url(database_url: str) -> str | None:
    prefix = "sqlite+aiosqlite:///"
    if database_url.startswith(prefix):
        return database_url.removeprefix(prefix)
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if value in {None, ""}:
        return None
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _parse_json(value: Any, default: Any) -> Any:
    if value in {None, ""}:
        return default
    if isinstance(value, (dict, list)):
        return value
    return json.loads(str(value))


def _event_params(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": uuid.UUID(str(row["id"])),
        "source": row["source"],
        "source_url": row["source_url"],
        "source_id": row["source_id"],
        "title": row["title"],
        "description": row["description"],
        "location_name": row["location_name"],
        "location_address": row["location_address"],
        "location_city": row["location_city"],
        "latitude": row["latitude"],
        "longitude": row["longitude"],
        "start_time": _parse_datetime(row["start_time"]),
        "end_time": _parse_datetime(row["end_time"]),
        "is_recurring": bool(row["is_recurring"]),
        "recurrence_rule": row["recurrence_rule"],
        "is_free": bool(row["is_free"]),
        "price_min": row["price_min"],
        "price_max": row["price_max"],
        "image_url": row["image_url"],
        "scraped_at": _parse_datetime(row["scraped_at"]),
        "raw_data": json.dumps(_parse_json(row["raw_data"], {})),
        "tags": json.dumps(_parse_json(row["tags"], {})) if row["tags"] else None,
        "tagged_at": _parse_datetime(row["tagged_at"]),
        "score_breakdown": (
            json.dumps(_parse_json(row["score_breakdown"], {})) if row["score_breakdown"] else None
        ),
        "attended": bool(row["attended"]),
    }


def _source_params(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": uuid.UUID(str(row["id"])),
        "name": row["name"],
        "url": row["url"],
        "domain": row["domain"],
        "city": row["city"],
        "category": row["category"],
        "user_id": uuid.UUID(str(row["user_id"])) if row["user_id"] else None,
        "builtin": bool(row["builtin"]),
        "recipe_json": row["recipe_json"],
        "enabled": bool(row["enabled"]),
        "status": row["status"],
        "last_scraped_at": _parse_datetime(row["last_scraped_at"]),
        "last_event_count": row["last_event_count"],
        "last_error": row["last_error"],
        "created_at": _parse_datetime(row["created_at"]),
        "updated_at": _parse_datetime(row["updated_at"]),
    }


def _user_params(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": uuid.UUID(str(row["id"])),
        "email": row["email"],
        "display_name": row["display_name"],
        "password_hash": row["password_hash"],
        "home_city": row["home_city"],
        "preferred_cities": json.dumps(_parse_json(row["preferred_cities"], [])),
        "theme": row["theme"],
        "notification_channels": json.dumps(_parse_json(row["notification_channels"], ["console"])),
        "email_to": row["email_to"],
        "sms_to": row["sms_to"],
        "child_name": row["child_name"],
        "onboarding_complete": bool(row["onboarding_complete"]),
        "interest_profile": json.dumps(_parse_json(row["interest_profile"], {})),
        "created_at": _parse_datetime(row["created_at"]),
        "updated_at": _parse_datetime(row["updated_at"]),
    }


def _job_params(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": uuid.UUID(str(row["id"])),
        "kind": row["kind"],
        "job_key": row["job_key"],
        "label": row["label"],
        "owner_user_id": uuid.UUID(str(row["owner_user_id"])),
        "source_id": uuid.UUID(str(row["source_id"])) if row["source_id"] else None,
        "state": row["state"],
        "detail": row["detail"],
        "result_json": row["result_json"],
        "error": row["error"],
        "created_at": _parse_datetime(row["created_at"]),
        "started_at": _parse_datetime(row["started_at"]),
        "finished_at": _parse_datetime(row["finished_at"]),
    }


INSERTS: dict[str, tuple[str, Callable[[sqlite3.Row], dict[str, Any]]]] = {
    "users": (
        """
        INSERT INTO users (
            id, email, display_name, password_hash, home_city, preferred_cities,
            theme, notification_channels, email_to, sms_to, child_name,
            onboarding_complete, interest_profile, created_at, updated_at
        ) VALUES (
            :id, :email, :display_name, :password_hash, :home_city, CAST(:preferred_cities AS jsonb),
            :theme, CAST(:notification_channels AS jsonb), :email_to, :sms_to, :child_name,
            :onboarding_complete, CAST(:interest_profile AS jsonb), :created_at, :updated_at
        )
        """,
        _user_params,
    ),
    "sources": (
        """
        INSERT INTO sources (
            id, name, url, domain, city, category, user_id, builtin, recipe_json,
            enabled, status, last_scraped_at, last_event_count, last_error, created_at, updated_at
        ) VALUES (
            :id, :name, :url, :domain, :city, :category, :user_id, :builtin, :recipe_json,
            :enabled, :status, :last_scraped_at, :last_event_count, :last_error, :created_at, :updated_at
        )
        """,
        _source_params,
    ),
    "events": (
        """
        INSERT INTO events (
            id, source, source_url, source_id, title, description, location_name,
            location_address, location_city, latitude, longitude, start_time, end_time,
            is_recurring, recurrence_rule, is_free, price_min, price_max, image_url,
            scraped_at, raw_data, tags, tagged_at, score_breakdown, attended
        ) VALUES (
            :id, :source, :source_url, :source_id, :title, :description, :location_name,
            :location_address, :location_city, :latitude, :longitude, :start_time, :end_time,
            :is_recurring, :recurrence_rule, :is_free, :price_min, :price_max, :image_url,
            :scraped_at, CAST(:raw_data AS jsonb), CAST(:tags AS jsonb), :tagged_at,
            CAST(:score_breakdown AS jsonb), :attended
        )
        """,
        _event_params,
    ),
    "jobs": (
        """
        INSERT INTO jobs (
            id, kind, job_key, label, owner_user_id, source_id, state, detail,
            result_json, error, created_at, started_at, finished_at
        ) VALUES (
            :id, :kind, :job_key, :label, :owner_user_id, :source_id, :state, :detail,
            :result_json, :error, :created_at, :started_at, :finished_at
        )
        """,
        _job_params,
    ),
}


def _iter_rows(
    conn: sqlite3.Connection, table: str, batch_size: int
) -> Iterable[list[sqlite3.Row]]:
    cursor = conn.execute(f"SELECT * FROM {table}")
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        yield rows


def _sqlite_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in TABLES
    }


async def _postgres_counts(conn: AsyncConnection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in TABLES:
        result = await conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
        counts[table] = int(result.scalar_one())
    return counts


async def _assert_tables_exist(conn: AsyncConnection) -> None:
    for table in TABLES:
        result = await conn.execute(text("SELECT to_regclass(:name)"), {"name": table})
        if result.scalar_one() is None:
            raise RuntimeError(
                f"Target Postgres table '{table}' is missing. Run 'uv run alembic upgrade head' first."
            )


async def _migrate_table(
    sqlite_conn: sqlite3.Connection,
    pg_conn: AsyncConnection,
    table: str,
    *,
    batch_size: int,
) -> int:
    insert_sql, row_mapper = INSERTS[table]
    migrated = 0
    for batch in _iter_rows(sqlite_conn, table, batch_size):
        await pg_conn.execute(text(insert_sql), [row_mapper(row) for row in batch])
        migrated += len(batch)
        print(f"[{table}] migrated {migrated}")
    return migrated


async def migrate(
    *, sqlite_path: str, postgres_url: str, batch_size: int, allow_non_empty: bool
) -> None:
    sqlite_db = Path(sqlite_path)
    if not sqlite_db.exists():
        raise FileNotFoundError(f"SQLite database not found: {sqlite_db}")
    if not postgres_url.startswith("postgresql+"):
        raise ValueError("Target URL must be a postgresql+asyncpg:// URL")

    sqlite_conn = sqlite3.connect(sqlite_db)
    sqlite_conn.row_factory = sqlite3.Row

    try:
        sqlite_counts = _sqlite_counts(sqlite_conn)
        print("Source SQLite counts:")
        for table, count in sqlite_counts.items():
            print(f"  {table}: {count}")

        engine = create_async_engine(postgres_url, future=True)
        try:
            async with engine.begin() as pg_conn:
                await _assert_tables_exist(pg_conn)
                existing_counts = await _postgres_counts(pg_conn)
                if any(existing_counts.values()) and not allow_non_empty:
                    rendered = ", ".join(
                        f"{table}={count}" for table, count in existing_counts.items()
                    )
                    raise RuntimeError(
                        "Target Postgres database is not empty. "
                        f"Pass --allow-non-empty to append into an existing database. Current counts: {rendered}"
                    )

                for table in TABLES:
                    print(f"Migrating {table}...")
                    await _migrate_table(sqlite_conn, pg_conn, table, batch_size=batch_size)

            async with engine.connect() as pg_conn:
                postgres_counts = await _postgres_counts(pg_conn)
        finally:
            await engine.dispose()

        print("\nTarget Postgres counts:")
        for table, count in postgres_counts.items():
            print(f"  {table}: {count}")

        mismatches = {
            table: (sqlite_counts[table], postgres_counts[table])
            for table in TABLES
            if sqlite_counts[table] != postgres_counts[table]
        }
        if mismatches:
            details = ", ".join(
                f"{table}: sqlite={sqlite_count}, postgres={postgres_count}"
                for table, (sqlite_count, postgres_count) in mismatches.items()
            )
            raise RuntimeError(f"Row-count verification failed: {details}")

        print("\nMigration complete. Row-count verification passed.")
    finally:
        sqlite_conn.close()


def parse_args() -> argparse.Namespace:
    settings = _load_settings()
    default_sqlite_path = _sqlite_path_from_url(settings.database_url) or settings.database_path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sqlite-path",
        default=default_sqlite_path,
        help=f"Path to the source SQLite database (default: {default_sqlite_path})",
    )
    parser.add_argument(
        "--postgres-url",
        default="",
        help="Target postgresql+asyncpg:// URL. Required unless DATABASE_URL already points at Postgres.",
    )
    parser.add_argument("--batch-size", type=int, default=500, help="Rows per insert batch")
    parser.add_argument(
        "--allow-non-empty",
        action="store_true",
        help="Allow migrating into a non-empty Postgres database",
    )
    return parser.parse_args()


def main() -> None:
    settings = _load_settings()
    args = parse_args()
    postgres_url = args.postgres_url or settings.database_url
    asyncio.run(
        migrate(
            sqlite_path=args.sqlite_path,
            postgres_url=postgres_url,
            batch_size=args.batch_size,
            allow_non_empty=args.allow_non_empty,
        )
    )


if __name__ == "__main__":
    main()
