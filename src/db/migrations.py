"""Database migration helpers for explicit Postgres schema checks."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_ALEMBIC_VERSION_TABLE = "alembic_version"


def alembic_config() -> Config:
    """Build an Alembic config rooted at the repository."""
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    return config


def expected_postgres_revision() -> str:
    """Return the single current Alembic head revision."""
    script = ScriptDirectory.from_config(alembic_config())
    head = script.get_current_head()
    if not head:
        raise RuntimeError("Alembic has no head revision configured")
    return head


def validate_postgres_revision(current_revision: str | None, expected_revision: str) -> None:
    """Raise when the connected Postgres schema is missing or out of date."""
    if current_revision == expected_revision:
        return
    if current_revision is None:
        raise RuntimeError(
            "Postgres schema is not initialized. Run 'make db-migrate' before starting the app."
        )
    raise RuntimeError(
        "Postgres schema is out of date "
        f"(current={current_revision}, expected={expected_revision}). "
        "Run 'make db-migrate' before starting the app."
    )


async def current_postgres_revision(connection: AsyncConnection) -> str | None:
    """Read the current Alembic revision from the connected Postgres database."""
    table_result = await connection.execute(
        text("SELECT to_regclass(:table_name)"),
        {"table_name": _ALEMBIC_VERSION_TABLE},
    )
    if table_result.scalar_one_or_none() is None:
        return None

    revision_result = await connection.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
    return revision_result.scalar_one_or_none()


async def ensure_postgres_schema_current(connection: AsyncConnection) -> None:
    """Fail fast if the connected Postgres schema is not at the Alembic head."""
    validate_postgres_revision(await current_postgres_revision(connection), expected_postgres_revision())
