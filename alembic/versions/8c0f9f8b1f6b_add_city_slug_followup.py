"""add city slug followup migration

Revision ID: 8c0f9f8b1f6b
Revises: 3d7f85fe4c1a
Create Date: 2026-03-13 19:10:00.000000

"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8c0f9f8b1f6b"
down_revision: Union[str, Sequence[str], None] = "3d7f85fe4c1a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PUNCT_RE = re.compile(r"[^\w\s-]")
_WHITESPACE_RE = re.compile(r"\s+")
_HYPHEN_RE = re.compile(r"[-_]+")


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = :table_name
              AND column_name = :column_name
            """
        ),
        {"table_name": table_name, "column_name": column_name},
    )
    return result.scalar_one_or_none() is not None


def _normalize_city_slug(city: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(city or ""))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = _WHITESPACE_RE.sub(" ", text.strip())
    text = _PUNCT_RE.sub("", text.lower())
    text = _HYPHEN_RE.sub("-", text.replace(" ", "-"))
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "unknown"


def _backfill_city_slug(table_name: str, source_column: str, fallback: str) -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(f"SELECT id, {source_column} FROM {table_name}")
    ).mappings()
    for row in rows:
        slug = _normalize_city_slug(row[source_column])
        if slug == "unknown":
            slug = fallback
        bind.execute(
            sa.text(f"UPDATE {table_name} SET city_slug = :city_slug WHERE id = :row_id"),
            {"city_slug": slug, "row_id": row["id"]},
        )


def upgrade() -> None:
    """Upgrade schema."""
    if not _column_exists("events", "city_slug"):
        op.add_column(
            "events",
            sa.Column("city_slug", sa.Text(), server_default="lafayette", nullable=False),
        )
    _backfill_city_slug("events", "location_city", "lafayette")
    op.execute("DROP INDEX IF EXISTS idx_events_city")
    op.execute("CREATE INDEX IF NOT EXISTS idx_events_city ON events (city_slug)")

    if not _column_exists("sources", "city_slug"):
        op.add_column(
            "sources",
            sa.Column("city_slug", sa.Text(), server_default="unknown", nullable=False),
        )
    _backfill_city_slug("sources", "city", "unknown")


def downgrade() -> None:
    """Downgrade schema."""
    if _column_exists("sources", "city_slug"):
        op.drop_column("sources", "city_slug")

    op.execute("DROP INDEX IF EXISTS idx_events_city")
    op.execute("CREATE INDEX IF NOT EXISTS idx_events_city ON events (location_city)")
    if _column_exists("events", "city_slug"):
        op.drop_column("events", "city_slug")
