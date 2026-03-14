"""Postgres-only database compatibility facade."""

from __future__ import annotations

from src.config import settings
from src.db.postgres import PostgresDatabase

POSTGRES_SCHEME_PREFIX = "postgresql+"

Database = PostgresDatabase


def _resolve_postgres_database_url(database_url: str | None = None) -> str:
    resolved_url = database_url or settings.database_url
    if not resolved_url.startswith(POSTGRES_SCHEME_PREFIX):
        raise ValueError(
            "DATABASE_URL must use a PostgreSQL async SQLAlchemy URL "
            f"({POSTGRES_SCHEME_PREFIX}...). Got: {resolved_url}"
        )
    return resolved_url


def create_database(
    db_path: str | None = None, database_url: str | None = None
) -> PostgresDatabase:
    """Create the configured Postgres database implementation."""
    if db_path is not None:
        raise ValueError(
            "SQLite db_path support has been removed. Configure DATABASE_URL "
            "with the local Docker Postgres instance instead."
        )
    return PostgresDatabase(database_url=_resolve_postgres_database_url(database_url))
