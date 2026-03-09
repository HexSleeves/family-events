"""SQLAlchemy schema definitions for the Postgres migration path."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB, UUID

metadata = MetaData()
json_type = JSONB().with_variant(Text(), "sqlite")
uuid_type = UUID(as_uuid=True)


events = Table(
    "events",
    metadata,
    Column("id", uuid_type, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("source", Text, nullable=False),
    Column("source_url", Text, nullable=False),
    Column("source_id", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("description", Text, nullable=False, server_default=""),
    Column("location_name", Text, nullable=False, server_default=""),
    Column("location_address", Text, nullable=False, server_default=""),
    Column("location_city", Text, nullable=False, server_default="Lafayette"),
    Column("latitude", Float),
    Column("longitude", Float),
    Column("start_time", DateTime(timezone=True), nullable=False),
    Column("end_time", DateTime(timezone=True)),
    Column("is_recurring", Boolean, nullable=False, server_default=text("false")),
    Column("recurrence_rule", Text),
    Column("is_free", Boolean, nullable=False, server_default=text("true")),
    Column("price_min", Float),
    Column("price_max", Float),
    Column("image_url", Text),
    Column("scraped_at", DateTime(timezone=True), nullable=False),
    Column("raw_data", json_type, nullable=False),
    Column("tags", json_type),
    Column("tagged_at", DateTime(timezone=True)),
    Column("score_breakdown", json_type),
    Column("attended", Boolean, nullable=False, server_default=text("false")),
    UniqueConstraint("source", "source_id", name="uq_events_source_source_id"),
)

Index("idx_events_start_time", events.c.start_time)
Index("idx_events_source", events.c.source, events.c.source_id)
Index("idx_events_city", events.c.location_city)
Index("idx_events_tagging_version", text("((tags->>'tagging_version'))"), postgresql_using="btree")
Index(
    "idx_events_toddler_score",
    text("(((tags->>'toddler_score')::integer))"),
    postgresql_using="btree",
    postgresql_where=text("tags IS NOT NULL"),
)
Index(
    "idx_events_untagged_start_time",
    events.c.start_time,
    postgresql_where=text("tags IS NULL"),
)
Index(
    "idx_events_title_trgm",
    text("lower(title) gin_trgm_ops"),
    postgresql_using="gin",
)
Index(
    "idx_events_description_trgm",
    text("lower(description) gin_trgm_ops"),
    postgresql_using="gin",
)

sources = Table(
    "sources",
    metadata,
    Column("id", uuid_type, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("name", Text, nullable=False),
    Column("url", Text, nullable=False, unique=True),
    Column("domain", Text, nullable=False),
    Column("city", Text, nullable=False, server_default=""),
    Column("category", Text, nullable=False, server_default="custom"),
    Column("user_id", uuid_type, ForeignKey("users.id", ondelete="CASCADE")),
    Column("builtin", Boolean, nullable=False, server_default=text("false")),
    Column("recipe_json", Text),
    Column("enabled", Boolean, nullable=False, server_default=text("true")),
    Column("status", Text, nullable=False, server_default="pending"),
    Column("last_scraped_at", DateTime(timezone=True)),
    Column("last_event_count", Integer, nullable=False, server_default="0"),
    Column("last_error", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "status IN ('pending', 'analyzing', 'active', 'stale', 'failed', 'disabled')",
        name="ck_sources_status_valid",
    ),
)

Index("idx_sources_user_id", sources.c.user_id)
Index("idx_sources_status_enabled", sources.c.status, sources.c.enabled)

users = Table(
    "users",
    metadata,
    Column("id", uuid_type, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("email", CITEXT(), nullable=False, unique=True),
    Column("display_name", Text, nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("home_city", Text, nullable=False, server_default=""),
    Column("preferred_cities", json_type, nullable=False),
    Column("theme", Text, nullable=False, server_default="auto"),
    Column("notification_channels", json_type, nullable=False),
    Column("email_to", Text, nullable=False, server_default=""),
    Column("sms_to", Text, nullable=False, server_default=""),
    Column("child_name", Text, nullable=False, server_default="Your Little One"),
    Column("onboarding_complete", Boolean, nullable=False, server_default=text("false")),
    Column("interest_profile", json_type, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("theme IN ('light', 'dark', 'auto')", name="ck_users_theme_valid"),
)

Index("idx_users_email", users.c.email)

jobs = Table(
    "jobs",
    metadata,
    Column("id", uuid_type, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("kind", Text, nullable=False),
    Column("job_key", Text, nullable=False),
    Column("label", Text, nullable=False),
    Column("owner_user_id", uuid_type, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("source_id", uuid_type, ForeignKey("sources.id", ondelete="SET NULL")),
    Column("state", Text, nullable=False, server_default="running"),
    Column("detail", Text, nullable=False, server_default="Queued"),
    Column("result_json", Text, nullable=False, server_default=""),
    Column("error", Text, nullable=False, server_default=""),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    CheckConstraint(
        "state IN ('running', 'succeeded', 'failed', 'cancelled')",
        name="ck_jobs_state_valid",
    ),
)

Index("idx_jobs_owner_created", jobs.c.owner_user_id, jobs.c.created_at)
Index("idx_jobs_source_created", jobs.c.source_id, jobs.c.created_at)
Index("idx_jobs_key_state", jobs.c.job_key, jobs.c.state)
