"""create initial postgres schema

Revision ID: 91dae90b6493
Revises:
Create Date: 2026-03-08 19:54:23.039935

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "91dae90b6493"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

jsonb = postgresql.JSONB(astext_type=sa.Text())
uuid = postgresql.UUID(as_uuid=False)


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "users",
        sa.Column("id", uuid, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("home_city", sa.Text(), server_default="", nullable=False),
        sa.Column("preferred_cities", jsonb, nullable=False),
        sa.Column("theme", sa.Text(), server_default="auto", nullable=False),
        sa.Column("notification_channels", jsonb, nullable=False),
        sa.Column("email_to", sa.Text(), server_default="", nullable=False),
        sa.Column("sms_to", sa.Text(), server_default="", nullable=False),
        sa.Column("child_name", sa.Text(), server_default="Your Little One", nullable=False),
        sa.Column("onboarding_complete", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("interest_profile", jsonb, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("theme IN ('light', 'dark', 'auto')", name="ck_users_theme_valid"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("idx_users_email", "users", ["email"])

    op.create_table(
        "sources",
        sa.Column("id", uuid, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("city", sa.Text(), server_default="", nullable=False),
        sa.Column("city_slug", sa.Text(), server_default="unknown", nullable=False),
        sa.Column("category", sa.Text(), server_default="custom", nullable=False),
        sa.Column("user_id", uuid, nullable=True),
        sa.Column("builtin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("recipe_json", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("last_scraped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'analyzing', 'active', 'stale', 'failed', 'disabled')",
            name="ck_sources_status_valid",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
    )
    op.create_index("idx_sources_user_id", "sources", ["user_id"])
    op.create_index("idx_sources_status_enabled", "sources", ["status", "enabled"])

    op.create_table(
        "events",
        sa.Column("id", uuid, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("location_name", sa.Text(), server_default="", nullable=False),
        sa.Column("location_address", sa.Text(), server_default="", nullable=False),
        sa.Column("location_city", sa.Text(), server_default="Lafayette", nullable=False),
        sa.Column("city_slug", sa.Text(), server_default="lafayette", nullable=False),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_recurring", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("recurrence_rule", sa.Text(), nullable=True),
        sa.Column("is_free", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("price_min", sa.Float(), nullable=True),
        sa.Column("price_max", sa.Float(), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("scraped_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_data", jsonb, nullable=False),
        sa.Column("tags", jsonb, nullable=True),
        sa.Column("tagged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("score_breakdown", jsonb, nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "source_id", name="uq_events_source_source_id"),
    )
    op.create_index("idx_events_start_time", "events", ["start_time"])
    op.create_index("idx_events_source", "events", ["source", "source_id"])
    op.create_index("idx_events_city", "events", ["city_slug"])
    op.execute(
        "CREATE INDEX idx_events_tagging_version ON events (((tags->>'tagging_version')))"
    )
    op.execute(
        "CREATE INDEX idx_events_toddler_score ON events ((((tags->>'toddler_score')::integer))) WHERE tags IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX idx_events_untagged_start_time ON events (start_time) WHERE tags IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_events_title_trgm ON events USING gin (lower(title) gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX idx_events_description_trgm ON events USING gin (lower(description) gin_trgm_ops)"
    )
    op.create_table(
        "user_event_state",
        sa.Column("user_id", uuid, nullable=False),
        sa.Column("event_id", uuid, nullable=False),
        sa.Column("saved", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("attended", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "event_id", name="uq_user_event_state_user_event"),
    )
    op.create_index(
        "idx_user_event_state_flags",
        "user_event_state",
        ["user_id", "saved", "attended"],
    )
    op.create_index(
        "idx_user_event_state_updated",
        "user_event_state",
        ["user_id", "updated_at"],
    )

    op.create_table(
        "jobs",
        sa.Column("id", uuid, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("job_key", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("owner_user_id", uuid, nullable=False),
        sa.Column("source_id", uuid, nullable=True),
        sa.Column("state", sa.Text(), server_default="running", nullable=False),
        sa.Column("detail", sa.Text(), server_default="Queued", nullable=False),
        sa.Column("result_json", sa.Text(), server_default="", nullable=False),
        sa.Column("error", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('running', 'succeeded', 'failed', 'cancelled')",
            name="ck_jobs_state_valid",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_jobs_owner_created", "jobs", ["owner_user_id", "created_at"])
    op.create_index("idx_jobs_source_created", "jobs", ["source_id", "created_at"])
    op.create_index("idx_jobs_key_state", "jobs", ["job_key", "state"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_user_event_state_updated", table_name="user_event_state")
    op.drop_index("idx_user_event_state_flags", table_name="user_event_state")
    op.drop_table("user_event_state")

    op.drop_index("idx_jobs_key_state", table_name="jobs")
    op.drop_index("idx_jobs_source_created", table_name="jobs")
    op.drop_index("idx_jobs_owner_created", table_name="jobs")
    op.drop_table("jobs")

    op.execute("DROP INDEX IF EXISTS idx_events_description_trgm")
    op.execute("DROP INDEX IF EXISTS idx_events_title_trgm")
    op.execute("DROP INDEX IF EXISTS idx_events_untagged_start_time")
    op.execute("DROP INDEX IF EXISTS idx_events_toddler_score")
    op.execute("DROP INDEX IF EXISTS idx_events_tagging_version")
    op.drop_index("idx_events_city", table_name="events")
    op.drop_index("idx_events_source", table_name="events")
    op.drop_index("idx_events_start_time", table_name="events")
    op.drop_table("events")

    op.drop_index("idx_sources_status_enabled", table_name="sources")
    op.drop_index("idx_sources_user_id", table_name="sources")
    op.drop_table("sources")

    op.drop_index("idx_users_email", table_name="users")
    op.drop_table("users")
