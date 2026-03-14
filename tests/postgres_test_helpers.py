from __future__ import annotations

import asyncio
import os
from typing import Any, TypeVar

import asyncpg
from sqlalchemy import text
from sqlalchemy.engine import make_url

from alembic import command
from src.config import settings
from src.db.database import create_database
from src.db.migrations import alembic_config

DEFAULT_POSTGRES_DATABASE_URL = (
    "postgresql+asyncpg://family_events:family_events@localhost:5433/family_events"
)
TEST_DATABASE_ENV_VAR = "TEST_DATABASE_URL"
TEST_DATABASE_SUFFIX = "_test"
TRUNCATE_TABLES = ("user_event_state", "jobs", "events", "sources", "users")
T = TypeVar("T")


def resolve_postgres_source_database_url() -> str:
    configured_url = settings.database_url
    if configured_url.startswith("postgresql+"):
        return configured_url
    return DEFAULT_POSTGRES_DATABASE_URL


def resolve_postgres_test_database_url() -> str:
    explicit_url = os.environ.get(TEST_DATABASE_ENV_VAR, "").strip()
    if explicit_url:
        return explicit_url

    source_url = make_url(resolve_postgres_source_database_url())
    source_database = source_url.database or "family_events"
    test_database = (
        source_database
        if source_database.endswith(TEST_DATABASE_SUFFIX)
        else f"{source_database}{TEST_DATABASE_SUFFIX}"
    )
    return source_url.set(database=test_database).render_as_string(hide_password=False)


async def _recreate_postgres_test_database(test_database_url: str) -> None:
    test_url = make_url(test_database_url)
    test_database = test_url.database
    if not test_database:
        raise RuntimeError(
            f"Postgres test database URL is missing a database name: {test_database_url}"
        )

    admin_url = test_url.set(drivername="postgresql", database="postgres")
    admin_conn = await asyncpg.connect(dsn=admin_url.render_as_string(hide_password=False))
    quoted_database = test_database.replace('"', '""')
    try:
        await admin_conn.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = $1
              AND pid <> pg_backend_pid()
            """,
            test_database,
        )
        await admin_conn.execute(f'DROP DATABASE IF EXISTS "{quoted_database}"')
        await admin_conn.execute(f'CREATE DATABASE "{quoted_database}"')
    finally:
        await admin_conn.close()


def bootstrap_postgres_test_database() -> str:
    test_database_url = resolve_postgres_test_database_url()
    asyncio.run(_recreate_postgres_test_database(test_database_url))
    config = alembic_config()
    original_database_url = settings.database_url
    settings.database_url = test_database_url
    try:
        command.upgrade(config, "head")
    finally:
        settings.database_url = original_database_url

    return test_database_url


async def reset_postgres_test_database(database_url: str) -> None:
    async with create_database(database_url=database_url) as db, db.session() as session:
        await session.execute(
            text(f"TRUNCATE TABLE {', '.join(TRUNCATE_TABLES)} RESTART IDENTITY CASCADE")
        )
        await session.commit()


async def _run_database_method(
    database_url: str,
    method_name: str,
    *args: Any,
    **kwargs: Any,
) -> T:
    async with create_database(database_url=database_url) as db:
        method = getattr(db, method_name)
        return await method(*args, **kwargs)


def run_database_method(
    database_url: str,
    method_name: str,
    *args: Any,
    **kwargs: Any,
) -> T:
    return asyncio.run(_run_database_method(database_url, method_name, *args, **kwargs))
