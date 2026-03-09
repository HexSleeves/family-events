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


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "events",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("location_name", sa.Text(), server_default="", nullable=False),
        sa.Column("location_address", sa.Text(), server_default="", nullable=False),
        sa.Column("location_city", sa.Text(), server_default="Lafayette", nullable=False),
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
        sa.Column("attended", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "source_id", name="uq_events_source_source_id"),
    )
    op.create_index("idx_events_start_time", "events", ["start_time"])
    op.create_index("idx_events_source", "events", ["source", "source_id"])
    op.create_index("idx_events_city", "events", ["location_city"])

    op.create_table(
        "sources",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("city", sa.Text(), server_default="", nullable=False),
        sa.Column("category", sa.Text(), server_default="custom", nullable=False),
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("builtin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("recipe_json", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("last_scraped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
    )
    op.create_index("idx_sources_user_id", "sources", ["user_id"])

    op.create_table(
        "users",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
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
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("idx_users_email", "users", ["email"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("job_key", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("owner_user_id", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), server_default="running", nullable=False),
        sa.Column("detail", sa.Text(), server_default="Queued", nullable=False),
        sa.Column("result_json", sa.Text(), server_default="", nullable=False),
        sa.Column("error", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_jobs_owner_created", "jobs", ["owner_user_id", "created_at"])
    op.create_index("idx_jobs_source_created", "jobs", ["source_id", "created_at"])
    op.create_index("idx_jobs_key_state", "jobs", ["job_key", "state"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_jobs_key_state", table_name="jobs")
    op.drop_index("idx_jobs_source_created", table_name="jobs")
    op.drop_index("idx_jobs_owner_created", table_name="jobs")
    op.drop_table("jobs")

    op.drop_index("idx_users_email", table_name="users")
    op.drop_table("users")

    op.drop_index("idx_sources_user_id", table_name="sources")
    op.drop_table("sources")

    op.drop_index("idx_events_city", table_name="events")
    op.drop_index("idx_events_source", table_name="events")
    op.drop_index("idx_events_start_time", table_name="events")
    op.drop_table("events")
