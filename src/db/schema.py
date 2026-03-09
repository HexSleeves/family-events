"""SQLAlchemy schema definitions for the Postgres migration path."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()
json_type = JSONB().with_variant(Text(), "sqlite")


events = Table(
    "events",
    metadata,
    Column("id", Text, primary_key=True),
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
    Column("is_recurring", Boolean, nullable=False, server_default="false"),
    Column("recurrence_rule", Text),
    Column("is_free", Boolean, nullable=False, server_default="true"),
    Column("price_min", Float),
    Column("price_max", Float),
    Column("image_url", Text),
    Column("scraped_at", DateTime(timezone=True), nullable=False),
    Column("raw_data", json_type, nullable=False),
    Column("tags", json_type),
    Column("tagged_at", DateTime(timezone=True)),
    Column("score_breakdown", json_type),
    Column("attended", Boolean, nullable=False, server_default="false"),
    UniqueConstraint("source", "source_id", name="uq_events_source_source_id"),
)

sources = Table(
    "sources",
    metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("url", Text, nullable=False, unique=True),
    Column("domain", Text, nullable=False),
    Column("city", Text, nullable=False, server_default=""),
    Column("category", Text, nullable=False, server_default="custom"),
    Column("user_id", Text),
    Column("builtin", Boolean, nullable=False, server_default="false"),
    Column("recipe_json", Text),
    Column("enabled", Boolean, nullable=False, server_default="true"),
    Column("status", Text, nullable=False, server_default="pending"),
    Column("last_scraped_at", DateTime(timezone=True)),
    Column("last_event_count", Integer, nullable=False, server_default="0"),
    Column("last_error", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

users = Table(
    "users",
    metadata,
    Column("id", Text, primary_key=True),
    Column("email", Text, nullable=False, unique=True),
    Column("display_name", Text, nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("home_city", Text, nullable=False, server_default=""),
    Column("preferred_cities", json_type, nullable=False),
    Column("theme", Text, nullable=False, server_default="auto"),
    Column("notification_channels", json_type, nullable=False),
    Column("email_to", Text, nullable=False, server_default=""),
    Column("sms_to", Text, nullable=False, server_default=""),
    Column("child_name", Text, nullable=False, server_default="Your Little One"),
    Column("onboarding_complete", Boolean, nullable=False, server_default="false"),
    Column("interest_profile", json_type, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

jobs = Table(
    "jobs",
    metadata,
    Column("id", Text, primary_key=True),
    Column("kind", Text, nullable=False),
    Column("job_key", Text, nullable=False),
    Column("label", Text, nullable=False),
    Column("owner_user_id", Text, nullable=False),
    Column("source_id", Text),
    Column("state", Text, nullable=False, server_default="running"),
    Column("detail", Text, nullable=False, server_default="Queued"),
    Column("result_json", Text, nullable=False, server_default=""),
    Column("error", Text, nullable=False, server_default=""),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
)
