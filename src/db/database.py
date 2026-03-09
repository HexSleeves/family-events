"""Async SQLite database layer using aiosqlite."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite

from src.config import settings
from src.db.postgres import PostgresDatabase

from .models import Event, EventTags, InterestProfile, Job, Source, User

DEDUP_DEBUG = os.environ.get("DEDUP_DEBUG", "").lower() in {"1", "true", "yes", "on"}
logger = logging.getLogger("uvicorn.error")

_CREATE_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS events (
    id              TEXT PRIMARY KEY,
    source          TEXT NOT NULL,
    source_url      TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    location_name   TEXT NOT NULL DEFAULT '',
    location_address TEXT NOT NULL DEFAULT '',
    location_city   TEXT NOT NULL DEFAULT 'Lafayette',
    latitude        REAL,
    longitude       REAL,
    start_time      TEXT NOT NULL,
    end_time        TEXT,
    is_recurring    INTEGER NOT NULL DEFAULT 0,
    recurrence_rule TEXT,
    is_free         INTEGER NOT NULL DEFAULT 1,
    price_min       REAL,
    price_max       REAL,
    image_url       TEXT,
    scraped_at      TEXT NOT NULL,
    raw_data        TEXT NOT NULL DEFAULT '{}',
    tags            TEXT,
    score_breakdown TEXT,
    attended        INTEGER NOT NULL DEFAULT 0,
    UNIQUE(source, source_id)
);
"""

_CREATE_SOURCES_TABLE = """
CREATE TABLE IF NOT EXISTS sources (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    url             TEXT NOT NULL UNIQUE,
    domain          TEXT NOT NULL,
    city            TEXT NOT NULL DEFAULT '',
    category        TEXT NOT NULL DEFAULT 'custom',
    user_id         TEXT,
    builtin         INTEGER NOT NULL DEFAULT 0,
    recipe_json     TEXT,
    enabled         INTEGER NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'pending',
    last_scraped_at TEXT,
    last_event_count INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
"""

_CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    id                    TEXT PRIMARY KEY,
    email                 TEXT NOT NULL UNIQUE,
    display_name          TEXT NOT NULL,
    password_hash         TEXT NOT NULL,
    home_city             TEXT NOT NULL DEFAULT '',
    preferred_cities      TEXT NOT NULL DEFAULT '[]',
    theme                 TEXT NOT NULL DEFAULT 'auto',
    notification_channels TEXT NOT NULL DEFAULT '["console"]',
    email_to              TEXT NOT NULL DEFAULT '',
    sms_to                TEXT NOT NULL DEFAULT '',
    child_name            TEXT NOT NULL DEFAULT 'Your Little One',
    onboarding_complete   INTEGER NOT NULL DEFAULT 0,
    interest_profile      TEXT NOT NULL DEFAULT '{}',
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);
"""

_CREATE_JOBS_TABLE = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    job_key       TEXT NOT NULL,
    label         TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    source_id     TEXT,
    state         TEXT NOT NULL DEFAULT 'running',
    detail        TEXT NOT NULL DEFAULT 'Queued',
    result_json   TEXT NOT NULL DEFAULT '',
    error         TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_events_start_time ON events(start_time);",
    "CREATE INDEX IF NOT EXISTS idx_events_source ON events(source, source_id);",
    "CREATE INDEX IF NOT EXISTS idx_events_city ON events(location_city);",
    "CREATE INDEX IF NOT EXISTS idx_events_tags ON events(tags) WHERE tags IS NULL;",
    "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);",
    "CREATE INDEX IF NOT EXISTS idx_sources_user_id ON sources(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_jobs_owner_created ON jobs(owner_user_id, created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_jobs_source_created ON jobs(source_id, created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_jobs_key_state ON jobs(job_key, state);",
]


def _row_to_event(row: aiosqlite.Row) -> Event:
    """Convert a database row (dict) to an Event model."""
    d = dict(row)
    # Booleans stored as 0/1
    d["is_recurring"] = bool(d["is_recurring"])
    d["is_free"] = bool(d["is_free"])
    d["attended"] = bool(d["attended"])
    # JSON fields
    d["raw_data"] = json.loads(str(d["raw_data"])) if d["raw_data"] else {}
    d["tags"] = EventTags.model_validate(json.loads(str(d["tags"]))) if d["tags"] else None
    d["score_breakdown"] = (
        json.loads(str(d["score_breakdown"])) if d.get("score_breakdown") else None
    )
    # Datetimes stored as ISO strings
    d["start_time"] = datetime.fromisoformat(str(d["start_time"]))
    d["end_time"] = datetime.fromisoformat(str(d["end_time"])) if d["end_time"] else None
    d["scraped_at"] = datetime.fromisoformat(str(d["scraped_at"]))
    return Event.model_validate(d)


def _row_to_user(row: aiosqlite.Row) -> User:
    """Convert a database row to a User model."""
    d = dict(row)
    d["preferred_cities"] = json.loads(str(d["preferred_cities"]))
    d["notification_channels"] = json.loads(str(d["notification_channels"]))
    d["sms_to"] = str(d.get("sms_to") or "")
    d["onboarding_complete"] = bool(d.get("onboarding_complete", 0))
    raw_profile = json.loads(str(d["interest_profile"])) if d["interest_profile"] else {}
    d["interest_profile"] = (
        InterestProfile.model_validate(raw_profile) if raw_profile else InterestProfile()
    )
    d["created_at"] = datetime.fromisoformat(str(d["created_at"]))
    d["updated_at"] = datetime.fromisoformat(str(d["updated_at"]))
    return User.model_validate(d)


def _row_to_source(row: aiosqlite.Row) -> Source:
    """Convert a database row to a Source model."""
    d = dict(row)
    d["builtin"] = bool(d["builtin"])
    d["enabled"] = bool(d["enabled"])
    d["city"] = str(d.get("city") or "")
    d["category"] = str(d.get("category") or "custom")
    d["last_scraped_at"] = (
        datetime.fromisoformat(str(d["last_scraped_at"])) if d["last_scraped_at"] else None
    )
    d["created_at"] = datetime.fromisoformat(str(d["created_at"]))
    d["updated_at"] = datetime.fromisoformat(str(d["updated_at"]))
    return Source.model_validate(d)


def _row_to_job(row: aiosqlite.Row) -> Job:
    """Convert a database row to a Job model."""
    d = dict(row)
    d["created_at"] = datetime.fromisoformat(str(d["created_at"]))
    d["started_at"] = datetime.fromisoformat(str(d["started_at"])) if d["started_at"] else None
    d["finished_at"] = datetime.fromisoformat(str(d["finished_at"])) if d["finished_at"] else None
    return Job.model_validate(d)


def _canonicalize_title(title: str) -> str:
    """Normalize title for fuzzy dedupe grouping."""
    text = title.lower().strip()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _event_fingerprint(event: Event) -> str:
    """Build a stable cross-source fingerprint for likely duplicate events."""
    date_part = event.start_time.date().isoformat()
    city = (event.location_city or "").lower().strip()
    title = _canonicalize_title(event.title)
    key = f"{title}|{date_part}|{city}"
    return hashlib.sha1(key.encode()).hexdigest()


def _title_similarity(a: str, b: str) -> float:
    """Token overlap similarity for fuzzy title matching."""
    a_tokens = set(_canonicalize_title(a).split())
    b_tokens = set(_canonicalize_title(b).split())
    if not a_tokens or not b_tokens:
        return 0.0
    overlap = len(a_tokens & b_tokens)
    union = len(a_tokens | b_tokens)
    return overlap / union if union else 0.0


def _event_to_params(event: Event) -> dict[str, Any]:
    """Convert an Event model to a dict of SQLite bind parameters."""
    return {
        "id": event.id,
        "source": event.source,
        "source_url": event.source_url,
        "source_id": event.source_id,
        "title": event.title,
        "description": event.description,
        "location_name": event.location_name,
        "location_address": event.location_address,
        "location_city": event.location_city,
        "latitude": event.latitude,
        "longitude": event.longitude,
        "start_time": event.start_time.isoformat(),
        "end_time": event.end_time.isoformat() if event.end_time else None,
        "is_recurring": int(event.is_recurring),
        "recurrence_rule": event.recurrence_rule,
        "is_free": int(event.is_free),
        "price_min": event.price_min,
        "price_max": event.price_max,
        "image_url": event.image_url,
        "scraped_at": event.scraped_at.isoformat(),
        "raw_data": json.dumps(event.raw_data),
        "tags": (json.dumps(event.tags.model_dump()) if event.tags else None),
        "score_breakdown": json.dumps(event.score_breakdown) if event.score_breakdown else None,
        "attended": int(event.attended),
    }


def _sqlite_path_from_url(database_url: str) -> str | None:
    prefix = "sqlite+aiosqlite:///"
    if database_url.startswith(prefix):
        return database_url.removeprefix(prefix)
    return None


class SqliteDatabase:
    """Async SQLite database for family events."""

    def __init__(self, db_path: str | None = None, database_url: str | None = None) -> None:
        self.database_url = database_url or settings.database_url
        self.db_path = db_path or _sqlite_path_from_url(self.database_url) or settings.database_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Open the database connection, enable WAL mode, and create tables."""
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._db.execute("PRAGMA foreign_keys=ON;")
        await self._db.execute(_CREATE_EVENTS_TABLE)
        await self._db.execute(_CREATE_SOURCES_TABLE)
        await self._db.execute(_CREATE_USERS_TABLE)
        await self._db.execute(_CREATE_JOBS_TABLE)
        # Migrations for existing databases
        for migration in [
            "ALTER TABLE sources ADD COLUMN user_id TEXT",
            "ALTER TABLE sources ADD COLUMN city TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE sources ADD COLUMN category TEXT NOT NULL DEFAULT 'custom'",
            "ALTER TABLE users ADD COLUMN email_to TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE users ADD COLUMN sms_to TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE users ADD COLUMN child_name TEXT NOT NULL DEFAULT 'Your Little One'",
            "ALTER TABLE users ADD COLUMN onboarding_complete INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE events ADD COLUMN tagged_at TEXT",
            "ALTER TABLE events ADD COLUMN score_breakdown TEXT",
        ]:
            with contextlib.suppress(Exception):
                await self._db.execute(migration)
        for idx_sql in _CREATE_INDEXES:
            await self._db.execute(idx_sql)
        await self._db.commit()

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._db

    # ------------------------------------------------------------------
    # Upsert
    # ------------------------------------------------------------------

    async def upsert_event(self, event: Event) -> str:
        """Insert or update an event, keyed on (source, source_id).

        Also applies cross-source dedupe using fuzzy match on title/date/city.
        Returns the canonical event id (existing id if deduped).
        """
        # 1) strict dedupe for source-local IDs
        async with self.db.execute(
            "SELECT id FROM events WHERE source = :source AND source_id = :source_id",
            {"source": event.source, "source_id": event.source_id},
        ) as cursor:
            existing = await cursor.fetchone()
        if existing:
            event.id = existing["id"]

        # 2) cross-source fuzzy dedupe (only if this source/source_id is new)
        if not existing:
            canonical_id, dedupe_reason = await self._find_duplicate_event_id(event)
            if canonical_id:
                params = _event_to_params(event)
                params["canonical_id"] = canonical_id
                await self.db.execute(
                    """
                    UPDATE events
                    SET description = CASE
                            WHEN (description IS NULL OR description = '') AND :description != '' THEN :description
                            ELSE description
                        END,
                        location_name = CASE
                            WHEN (location_name IS NULL OR location_name = '') AND :location_name != '' THEN :location_name
                            ELSE location_name
                        END,
                        location_address = CASE
                            WHEN (location_address IS NULL OR location_address = '') AND :location_address != '' THEN :location_address
                            ELSE location_address
                        END,
                        latitude = COALESCE(latitude, :latitude),
                        longitude = COALESCE(longitude, :longitude),
                        end_time = COALESCE(end_time, :end_time),
                        image_url = COALESCE(image_url, :image_url),
                        scraped_at = CASE
                            WHEN scraped_at < :scraped_at THEN :scraped_at
                            ELSE scraped_at
                        END,
                        tags = COALESCE(tags, :tags),
                        score_breakdown = COALESCE(score_breakdown, :score_breakdown),
                        attended = CASE WHEN attended = 1 OR :attended = 1 THEN 1 ELSE 0 END
                    WHERE id = :canonical_id
                    """,
                    params,
                )
                await self.db.commit()
                if DEDUP_DEBUG:
                    logger.info(
                        "dedupe: merged event source=%s source_id=%s into id=%s reason=%s title=%s",
                        event.source,
                        event.source_id,
                        canonical_id,
                        dedupe_reason or "unknown",
                        event.title,
                    )
                return canonical_id

        params = _event_to_params(event)

        await self.db.execute(
            """
            INSERT INTO events (
                id, source, source_url, source_id, title, description,
                location_name, location_address, location_city,
                latitude, longitude, start_time, end_time,
                is_recurring, recurrence_rule, is_free,
                price_min, price_max, image_url,
                scraped_at, raw_data, tags, score_breakdown, attended
            ) VALUES (
                :id, :source, :source_url, :source_id, :title, :description,
                :location_name, :location_address, :location_city,
                :latitude, :longitude, :start_time, :end_time,
                :is_recurring, :recurrence_rule, :is_free,
                :price_min, :price_max, :image_url,
                :scraped_at, :raw_data, :tags, :score_breakdown, :attended
            )
            ON CONFLICT(source, source_id) DO UPDATE SET
                source_url      = excluded.source_url,
                title           = excluded.title,
                description     = excluded.description,
                location_name   = excluded.location_name,
                location_address = excluded.location_address,
                location_city   = excluded.location_city,
                latitude        = excluded.latitude,
                longitude       = excluded.longitude,
                start_time      = excluded.start_time,
                end_time        = excluded.end_time,
                is_recurring    = excluded.is_recurring,
                recurrence_rule = excluded.recurrence_rule,
                is_free         = excluded.is_free,
                price_min       = excluded.price_min,
                price_max       = excluded.price_max,
                image_url       = excluded.image_url,
                scraped_at      = excluded.scraped_at,
                raw_data        = excluded.raw_data
            """,
            params,
        )
        await self.db.commit()

        # Return the actual id (may differ if row already existed)
        async with self.db.execute(
            "SELECT id FROM events WHERE source = :source AND source_id = :source_id",
            {"source": event.source, "source_id": event.source_id},
        ) as cursor:
            row = await cursor.fetchone()
            return row["id"] if row else event.id

    async def _find_duplicate_event_id(self, event: Event) -> tuple[str | None, str | None]:
        """Find a likely duplicate event across sources.

        Returns (event_id, reason).
        """
        start = event.start_time - timedelta(hours=4)
        end = event.start_time + timedelta(hours=4)
        async with self.db.execute(
            """
            SELECT id, title, source, source_id, start_time, location_city
            FROM events
            WHERE location_city = :city
              AND start_time >= :start
              AND start_time <= :end
            """,
            {
                "city": event.location_city,
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
        ) as cursor:
            rows = await cursor.fetchall()

        fp = _event_fingerprint(event)
        for row in rows:
            candidate_fp = hashlib.sha1(
                (
                    f"{_canonicalize_title(str(row['title']))}|"
                    f"{datetime.fromisoformat(str(row['start_time'])).date().isoformat()}|"
                    f"{str(row['location_city']).lower().strip()}"
                ).encode()
            ).hexdigest()
            if candidate_fp == fp:
                return str(row["id"]), "fingerprint"

        for row in rows:
            similarity = _title_similarity(event.title, str(row["title"]))
            if similarity >= 0.75:
                return str(row["id"]), f"title_similarity:{similarity:.2f}"

        return None, None

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get_events_for_weekend(self, sat_date: str, sun_date: str) -> list[Event]:
        """Return events whose start_time falls on the given Saturday or Sunday.

        Dates should be ISO date strings like '2025-07-12'.
        """
        async with self.db.execute(
            """
            SELECT * FROM events
            WHERE start_time >= :sat_start
              AND start_time < :mon_start
            ORDER BY start_time
            """,
            {
                "sat_start": f"{sat_date}T00:00:00",
                "mon_start": f"{sun_date}T23:59:59",
            },
        ) as cursor:
            rows = await cursor.fetchall()
            return [_row_to_event(r) for r in rows]

    async def get_untagged_events(
        self,
        *,
        tagging_version: str | None = None,
        include_stale: bool = True,
    ) -> list[Event]:
        """Return events that have no tags, or stale tags when requested."""
        if tagging_version is None or not include_stale:
            query = "SELECT * FROM events WHERE tags IS NULL ORDER BY start_time"
            params: dict[str, Any] = {}
        else:
            query = (
                "SELECT * FROM events "
                "WHERE tags IS NULL "
                "OR COALESCE(json_extract(tags, '$.tagging_version'), '') != :tagging_version "
                "ORDER BY start_time"
            )
            params = {"tagging_version": tagging_version}
        async with self.db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [_row_to_event(r) for r in rows]

    async def count_stale_tagged_events(self, *, tagging_version: str) -> int:
        """Count events tagged with an older tagging version."""
        async with self.db.execute(
            "SELECT COUNT(*) FROM events WHERE tags IS NOT NULL AND COALESCE(json_extract(tags, '$.tagging_version'), '') != :tagging_version",
            {"tagging_version": tagging_version},
        ) as cursor:
            row = await cursor.fetchone()
            return int(row[0] if row else 0)

    async def update_event_tags(
        self,
        event_id: str,
        tags: EventTags,
        *,
        score_breakdown: dict[str, float] | None = None,
    ) -> None:
        """Set the tags JSON for a specific event."""
        now = datetime.now(tz=UTC).isoformat()
        await self.db.execute(
            "UPDATE events SET tags = :tags, score_breakdown = :score_breakdown, tagged_at = :tagged_at WHERE id = :id",
            {
                "tags": json.dumps(tags.model_dump()),
                "score_breakdown": json.dumps(score_breakdown) if score_breakdown else None,
                "tagged_at": now,
                "id": event_id,
            },
        )
        await self.db.commit()

    async def get_pipeline_timestamps(self) -> dict[str, datetime | None]:
        """Return last scrape and tag timestamps from events table."""
        async with self.db.execute(
            "SELECT MAX(scraped_at) AS last_scraped_at, MAX(tagged_at) AS last_tagged_at FROM events"
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return {"last_scraped_at": None, "last_tagged_at": None}
            last_scraped = row["last_scraped_at"]
            last_tagged = row["last_tagged_at"]
            return {
                "last_scraped_at": datetime.fromisoformat(str(last_scraped))
                if last_scraped
                else None,
                "last_tagged_at": datetime.fromisoformat(str(last_tagged)) if last_tagged else None,
            }

    async def get_recent_events(self, days: int = 14) -> list[Event]:
        """Return events with start_time within the next `days` days."""
        now = datetime.now(tz=UTC).isoformat()
        future = (datetime.now(tz=UTC) + timedelta(days=days)).isoformat()
        async with self.db.execute(
            """
            SELECT * FROM events
            WHERE start_time >= :now AND start_time <= :future
            ORDER BY start_time
            """,
            {"now": now, "future": future},
        ) as cursor:
            rows = await cursor.fetchall()
            return [_row_to_event(r) for r in rows]

    async def get_events_between(
        self,
        start: datetime,
        end: datetime,
        *,
        attended: str = "",
    ) -> list[Event]:
        """Return events in [start, end), optionally filtering attended status."""
        conditions = ["start_time >= :start", "start_time < :end"]
        params: dict[str, Any] = {"start": start.isoformat(), "end": end.isoformat()}

        if attended == "yes":
            conditions.append("attended = 1")
        elif attended == "no":
            conditions.append("attended = 0")

        where = " AND ".join(conditions)
        async with self.db.execute(
            f"""
            SELECT * FROM events
            WHERE {where}
            ORDER BY start_time
            """,
            params,
        ) as cursor:
            rows = await cursor.fetchall()
            return [_row_to_event(r) for r in rows]

    async def search_events(
        self,
        *,
        days: int = 30,
        q: str = "",
        city: str = "",
        source: str = "",
        tagged: str = "",
        attended: str = "",
        score_min: int | None = None,
        sort: str = "start_time",
        page: int = 1,
        per_page: int = 25,
    ) -> tuple[list[Event], int]:
        """Search events with filters, returning (events, total_count).

        Args:
            days: Only include events starting within this many days from now.
            q: Full-text search on title and description.
            city: Filter by location_city (exact match).
            source: Filter by source (exact match).
            tagged: "yes" for tagged only, "no" for untagged only, "" for all.
            attended: "yes" for attended only, "no" for unattended only, "" for all.
            score_min: Minimum toddler_score (requires tagged).
            sort: Column to sort by. Prefix with "-" for descending.
            page: 1-based page number.
            per_page: Results per page.

        Returns:
            Tuple of (list of events, total matching count).
        """
        now = datetime.now(tz=UTC).isoformat()
        future = (datetime.now(tz=UTC) + timedelta(days=days)).isoformat()

        conditions = ["start_time >= :now", "start_time <= :future"]
        params: dict[str, Any] = {"now": now, "future": future}

        if q:
            conditions.append("(title LIKE :q OR description LIKE :q)")
            params["q"] = f"%{q}%"

        if city:
            conditions.append("location_city = :city")
            params["city"] = city

        if source:
            conditions.append("source = :source")
            params["source"] = source

        if tagged == "yes":
            conditions.append("tags IS NOT NULL")
        elif tagged == "no":
            conditions.append("tags IS NULL")

        if attended == "yes":
            conditions.append("attended = 1")
        elif attended == "no":
            conditions.append("attended = 0")

        if score_min is not None:
            conditions.append(
                "tags IS NOT NULL AND CAST(json_extract(tags, '$.toddler_score') AS INTEGER) >= :score_min"
            )
            params["score_min"] = score_min

        where = " AND ".join(conditions)

        # Count total
        count_sql = f"SELECT COUNT(*) FROM events WHERE {where}"
        async with self.db.execute(count_sql, params) as cursor:
            row = await cursor.fetchone()
            total = row[0] if row else 0

        # Determine sort
        _valid_sorts = {
            "start_time": "start_time",
            "-start_time": "start_time DESC",
            "title": "title",
            "-title": "title DESC",
            "city": "location_city",
            "-city": "location_city DESC",
            "source": "source",
            "-source": "source DESC",
            "score": "CAST(json_extract(tags, '$.toddler_score') AS INTEGER)",
            "-score": "CAST(json_extract(tags, '$.toddler_score') AS INTEGER) DESC",
        }
        order_clause = _valid_sorts.get(sort, "start_time")

        offset = (page - 1) * per_page
        params["limit"] = per_page
        params["offset"] = offset

        query_sql = f"""
            SELECT * FROM events
            WHERE {where}
            ORDER BY {order_clause}
            LIMIT :limit OFFSET :offset
        """
        async with self.db.execute(query_sql, params) as cursor:
            rows = await cursor.fetchall()
            events = [_row_to_event(r) for r in rows]

        return events, int(total)

    async def get_filter_options(self) -> dict[str, list[str]]:
        """Return distinct values for filter dropdowns."""
        cities: list[str] = []
        async with self.db.execute(
            "SELECT DISTINCT location_city FROM events ORDER BY location_city"
        ) as cursor:
            cities = [row[0] for row in await cursor.fetchall()]

        sources: list[str] = []
        async with self.db.execute("SELECT DISTINCT source FROM events ORDER BY source") as cursor:
            sources = [row[0] for row in await cursor.fetchall()]

        return {"cities": cities, "sources": sources}

    # ------------------------------------------------------------------
    # Sources CRUD
    # ------------------------------------------------------------------

    async def create_source(self, source: Source) -> str:
        """Insert a new source. Returns the source id."""
        await self.db.execute(
            """
            INSERT INTO sources (
                id, name, url, domain, city, category, user_id, builtin, recipe_json,
                enabled, status, last_scraped_at, last_event_count,
                last_error, created_at, updated_at
            ) VALUES (
                :id, :name, :url, :domain, :city, :category, :user_id, :builtin, :recipe_json,
                :enabled, :status, :last_scraped_at, :last_event_count,
                :last_error, :created_at, :updated_at
            )
            """,
            {
                "id": source.id,
                "name": source.name,
                "url": source.url,
                "domain": source.domain,
                "city": source.city,
                "category": source.category,
                "user_id": source.user_id,
                "builtin": int(source.builtin),
                "recipe_json": source.recipe_json,
                "enabled": int(source.enabled),
                "status": source.status,
                "last_scraped_at": (
                    source.last_scraped_at.isoformat() if source.last_scraped_at else None
                ),
                "last_event_count": source.last_event_count,
                "last_error": source.last_error,
                "created_at": source.created_at.isoformat(),
                "updated_at": source.updated_at.isoformat(),
            },
        )
        await self.db.commit()
        return source.id

    async def get_event(self, event_id: str) -> Event | None:
        """Get a single event by id."""
        async with self.db.execute("SELECT * FROM events WHERE id = :id", {"id": event_id}) as cursor:
            row = await cursor.fetchone()
            return _row_to_event(row) if row else None

    async def get_source(self, source_id: str) -> Source | None:
        """Get a single source by id."""
        async with self.db.execute(
            "SELECT * FROM sources WHERE id = :id", {"id": source_id}
        ) as cursor:
            row = await cursor.fetchone()
            return _row_to_source(row) if row else None

    async def get_source_by_url(self, url: str) -> Source | None:
        """Get a source by URL (for duplicate detection)."""
        async with self.db.execute(
            "SELECT * FROM sources WHERE url = :url", {"url": url}
        ) as cursor:
            row = await cursor.fetchone()
            return _row_to_source(row) if row else None

    async def get_all_sources(self) -> list[Source]:
        """Get all sources, ordered by created_at desc."""
        async with self.db.execute("SELECT * FROM sources ORDER BY created_at DESC") as cursor:
            rows = await cursor.fetchall()
            return [_row_to_source(r) for r in rows]

    async def get_enabled_sources(self) -> list[Source]:
        """Get enabled, non-builtin sources with recipes."""
        async with self.db.execute(
            """
            SELECT * FROM sources
            WHERE enabled = 1 AND builtin = 0
              AND status IN ('active', 'stale')
            ORDER BY created_at
            """
        ) as cursor:
            rows = await cursor.fetchall()
            return [_row_to_source(r) for r in rows]

    async def update_source_recipe(
        self, source_id: str, recipe_json: str, status: str = "active"
    ) -> None:
        """Save a generated recipe for a source."""
        now = datetime.now(tz=UTC).isoformat()
        await self.db.execute(
            """
            UPDATE sources
            SET recipe_json = :recipe_json, status = :status, updated_at = :now
            WHERE id = :id
            """,
            {"recipe_json": recipe_json, "status": status, "now": now, "id": source_id},
        )
        await self.db.commit()

    async def update_source_status(
        self,
        source_id: str,
        *,
        status: str | None = None,
        count: int | None = None,
        error: str | None = None,
    ) -> None:
        """Update scrape results on a source."""
        now = datetime.now(tz=UTC).isoformat()
        sets = ["updated_at = :now"]
        params: dict[str, Any] = {"now": now, "id": source_id}
        if status is not None:
            sets.append("status = :status")
            params["status"] = status
        if count is not None:
            sets.append("last_event_count = :count")
            sets.append("last_scraped_at = :now")
            sets.append("last_error = NULL")
            params["count"] = count
            if count == 0:
                sets.append("status = 'stale'")
            elif status is None:
                sets.append("status = 'active'")
        if error is not None:
            sets.append("last_error = :error")
            params["error"] = error
        sql = f"UPDATE sources SET {', '.join(sets)} WHERE id = :id"
        await self.db.execute(sql, params)
        await self.db.commit()

    async def toggle_source(self, source_id: str) -> bool:
        """Toggle enabled/disabled. Returns new enabled state."""
        now = datetime.now(tz=UTC).isoformat()
        await self.db.execute(
            """
            UPDATE sources
            SET enabled = CASE WHEN enabled = 1 THEN 0 ELSE 1 END,
                status = CASE WHEN enabled = 1 THEN 'disabled' ELSE 'active' END,
                updated_at = :now
            WHERE id = :id
            """,
            {"now": now, "id": source_id},
        )
        await self.db.commit()
        source = await self.get_source(source_id)
        return source.enabled if source else False

    async def delete_source(self, source_id: str) -> None:
        """Delete a source and all its events."""
        source = await self.get_source(source_id)
        if source and not source.builtin:
            await self.db.execute(
                "DELETE FROM events WHERE source = :source",
                {"source": f"custom:{source_id}"},
            )
        await self.db.execute("DELETE FROM sources WHERE id = :id", {"id": source_id})
        await self.db.commit()

    async def mark_attended(self, event_id: str) -> None:
        """Mark an event as attended."""
        await self.set_attended(event_id, attended=True)

    async def set_attended(self, event_id: str, *, attended: bool) -> None:
        """Set attendance flag for a single event."""
        await self.db.execute(
            "UPDATE events SET attended = :attended WHERE id = :id",
            {"attended": int(attended), "id": event_id},
        )
        await self.db.commit()

    async def set_attended_bulk(self, event_ids: list[str], *, attended: bool) -> None:
        """Set attendance for multiple events."""
        if not event_ids:
            return
        await self.db.executemany(
            "UPDATE events SET attended = ? WHERE id = ?",
            [(int(attended), event_id) for event_id in event_ids],
        )
        await self.db.commit()

    # ------------------------------------------------------------------
    # Jobs CRUD
    # ------------------------------------------------------------------

    async def create_job(self, job: Job) -> str:
        """Insert a persisted web job record."""
        await self.db.execute(
            """
            INSERT INTO jobs (
                id, kind, job_key, label, owner_user_id, source_id,
                state, detail, result_json, error,
                created_at, started_at, finished_at
            ) VALUES (
                :id, :kind, :job_key, :label, :owner_user_id, :source_id,
                :state, :detail, :result_json, :error,
                :created_at, :started_at, :finished_at
            )
            """,
            {
                "id": job.id,
                "kind": job.kind,
                "job_key": job.job_key,
                "label": job.label,
                "owner_user_id": job.owner_user_id,
                "source_id": job.source_id,
                "state": job.state,
                "detail": job.detail,
                "result_json": job.result_json,
                "error": job.error,
                "created_at": job.created_at.isoformat(),
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            },
        )
        await self.db.commit()
        return job.id

    async def update_job(self, job_id: str, **fields: Any) -> None:
        """Update selected persisted job fields."""
        allowed = {
            "state",
            "detail",
            "result_json",
            "error",
            "started_at",
            "finished_at",
        }
        sets: list[str] = []
        params: dict[str, Any] = {"id": job_id}
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key in {"started_at", "finished_at"} and value is not None:
                value = value.isoformat() if hasattr(value, "isoformat") else value
            sets.append(f"{key} = :{key}")
            params[key] = value
        if not sets:
            return
        await self.db.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = :id", params)
        await self.db.commit()

    async def get_job(self, job_id: str) -> Job | None:
        """Get a persisted job by id."""
        async with self.db.execute("SELECT * FROM jobs WHERE id = :id", {"id": job_id}) as cursor:
            row = await cursor.fetchone()
            return _row_to_job(row) if row else None

    async def get_active_job_by_key(self, job_key: str) -> Job | None:
        """Return the newest active job for a logical key, if any."""
        async with self.db.execute(
            """
            SELECT * FROM jobs
            WHERE job_key = :job_key AND state = 'running'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            {"job_key": job_key},
        ) as cursor:
            row = await cursor.fetchone()
            return _row_to_job(row) if row else None

    async def list_jobs(
        self,
        *,
        owner_user_id: str,
        source_id: str | None = None,
        state: str | None = None,
        kind: str | None = None,
        q: str = "",
        limit: int = 20,
    ) -> list[Job]:
        """List recent jobs for a user with optional filters."""
        sql = "SELECT * FROM jobs WHERE owner_user_id = :owner_user_id"
        params: dict[str, Any] = {"owner_user_id": owner_user_id, "limit": limit}
        if source_id is not None:
            sql += " AND source_id = :source_id"
            params["source_id"] = source_id
        if state:
            sql += " AND state = :state"
            params["state"] = state
        if kind:
            sql += " AND kind = :kind"
            params["kind"] = kind
        q = q.strip()
        if q:
            sql += " AND (label LIKE :q OR detail LIKE :q OR error LIKE :q)"
            params["q"] = f"%{q}%"
        sql += " ORDER BY created_at DESC LIMIT :limit"
        async with self.db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
            return [_row_to_job(r) for r in rows]

    async def list_job_kinds(self, *, owner_user_id: str) -> list[str]:
        """List distinct job kinds for a user."""
        async with self.db.execute(
            """
            SELECT DISTINCT kind FROM jobs
            WHERE owner_user_id = :owner_user_id
            ORDER BY kind ASC
            """,
            {"owner_user_id": owner_user_id},
        ) as cursor:
            rows = await cursor.fetchall()
            return [str(row["kind"]) for row in rows if row["kind"]]

    async def fail_stale_jobs(self, *, max_age_seconds: int) -> int:
        """Mark long-running jobs failed so stale records stop blocking new work."""
        now = datetime.now(tz=UTC).isoformat()
        cutoff = (datetime.now(tz=UTC) - timedelta(seconds=max_age_seconds)).isoformat()
        async with self.db.execute(
            """
            UPDATE jobs
            SET state = 'failed',
                detail = 'Failed',
                error = CASE
                    WHEN error != '' THEN error
                    ELSE 'Job exceeded max runtime or worker stopped unexpectedly'
                END,
                finished_at = :now
            WHERE state = 'running'
              AND COALESCE(started_at, created_at) < :cutoff
            """,
            {"now": now, "cutoff": cutoff},
        ) as cursor:
            await self.db.commit()
            return cursor.rowcount or 0

    # ------------------------------------------------------------------
    # Users CRUD
    # ------------------------------------------------------------------

    async def create_user(self, user: User) -> str:
        """Insert a new user. Returns the user id."""
        await self.db.execute(
            """
            INSERT INTO users (
                id, email, display_name, password_hash,
                home_city, preferred_cities, theme,
                notification_channels, email_to, sms_to, child_name, onboarding_complete,
                interest_profile, created_at, updated_at
            ) VALUES (
                :id, :email, :display_name, :password_hash,
                :home_city, :preferred_cities, :theme,
                :notification_channels, :email_to, :sms_to, :child_name, :onboarding_complete,
                :interest_profile, :created_at, :updated_at
            )
            """,
            {
                "id": user.id,
                "email": user.email,
                "display_name": user.display_name,
                "password_hash": user.password_hash,
                "home_city": user.home_city,
                "preferred_cities": json.dumps(user.preferred_cities),
                "theme": user.theme,
                "notification_channels": json.dumps(user.notification_channels),
                "email_to": user.email_to,
                "sms_to": user.sms_to,
                "child_name": user.child_name,
                "onboarding_complete": int(user.onboarding_complete),
                "interest_profile": json.dumps(user.interest_profile.model_dump()),
                "created_at": user.created_at.isoformat(),
                "updated_at": user.updated_at.isoformat(),
            },
        )
        await self.db.commit()
        return user.id

    async def get_user(self, user_id: str) -> User | None:
        """Get a user by id."""
        async with self.db.execute("SELECT * FROM users WHERE id = :id", {"id": user_id}) as cursor:
            row = await cursor.fetchone()
            return _row_to_user(row) if row else None

    async def get_user_by_email(self, email: str) -> User | None:
        """Get a user by email address."""
        async with self.db.execute(
            "SELECT * FROM users WHERE email = :email", {"email": email.lower().strip()}
        ) as cursor:
            row = await cursor.fetchone()
            return _row_to_user(row) if row else None

    async def update_user(self, user_id: str, **fields: Any) -> None:
        """Update specific fields on a user."""
        allowed = {
            "display_name",
            "home_city",
            "preferred_cities",
            "theme",
            "notification_channels",
            "email_to",
            "sms_to",
            "child_name",
            "onboarding_complete",
            "interest_profile",
            "password_hash",
        }
        now = datetime.now(tz=UTC).isoformat()
        sets = ["updated_at = :now"]
        params: dict[str, Any] = {"now": now, "id": user_id}
        for key, val in fields.items():
            if key not in allowed:
                continue
            if key in ("preferred_cities", "notification_channels"):
                val = json.dumps(val)
            elif key == "onboarding_complete":
                val = int(bool(val))
            elif key == "interest_profile":
                val = json.dumps(val.model_dump() if hasattr(val, "model_dump") else val)
            sets.append(f"{key} = :{key}")
            params[key] = val
        sql = f"UPDATE users SET {', '.join(sets)} WHERE id = :id"
        await self.db.execute(sql, params)
        await self.db.commit()

    async def get_all_users(self) -> list[User]:
        """Get all users."""
        async with self.db.execute("SELECT * FROM users ORDER BY created_at") as cursor:
            rows = await cursor.fetchall()
            return [_row_to_user(r) for r in rows]

    async def get_user_sources(self, user_id: str) -> list[Source]:
        """Get sources belonging to a specific user."""
        async with self.db.execute(
            "SELECT * FROM sources WHERE user_id = :uid ORDER BY builtin DESC, city ASC, name ASC, created_at DESC",
            {"uid": user_id},
        ) as cursor:
            rows = await cursor.fetchall()
            return [_row_to_source(r) for r in rows]

    async def get_user_source_by_url(self, user_id: str, url: str) -> Source | None:
        """Get a source by URL scoped to a user."""
        async with self.db.execute(
            "SELECT * FROM sources WHERE user_id = :user_id AND url = :url",
            {"user_id": user_id, "url": url},
        ) as cursor:
            row = await cursor.fetchone()
            return _row_to_source(row) if row else None

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    async def __aenter__(self) -> Database:
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def dedupe_existing_events(self) -> dict[str, int]:
        """One-time backfill dedupe across already-stored events.

        Keeps earliest-created canonical event id and merges newer duplicates into it.
        """
        async with self.db.execute(
            "SELECT * FROM events ORDER BY start_time, scraped_at"
        ) as cursor:
            rows = await cursor.fetchall()

        events = [_row_to_event(r) for r in rows]
        total = len(events)
        merged = 0

        # group by rough time bucket + city to keep complexity reasonable
        buckets: dict[str, list[Event]] = {}
        for event in events:
            bucket_key = (
                f"{event.location_city.lower().strip()}|"
                f"{event.start_time.date().isoformat()}|"
                f"{event.start_time.hour // 2}"
            )
            buckets.setdefault(bucket_key, []).append(event)

        for bucket_events in buckets.values():
            if len(bucket_events) < 2:
                continue
            canonical: list[Event] = []
            for event in bucket_events:
                duplicate_of: Event | None = None
                for c in canonical:
                    fp_a = _event_fingerprint(event)
                    fp_b = _event_fingerprint(c)
                    sim = _title_similarity(event.title, c.title)
                    if fp_a == fp_b or sim >= 0.75:
                        duplicate_of = c
                        break
                if not duplicate_of:
                    canonical.append(event)
                    continue

                params = _event_to_params(event)
                params["canonical_id"] = duplicate_of.id
                await self.db.execute(
                    """
                    UPDATE events
                    SET description = CASE
                            WHEN (description IS NULL OR description = '') AND :description != '' THEN :description
                            ELSE description
                        END,
                        location_name = CASE
                            WHEN (location_name IS NULL OR location_name = '') AND :location_name != '' THEN :location_name
                            ELSE location_name
                        END,
                        location_address = CASE
                            WHEN (location_address IS NULL OR location_address = '') AND :location_address != '' THEN :location_address
                            ELSE location_address
                        END,
                        latitude = COALESCE(latitude, :latitude),
                        longitude = COALESCE(longitude, :longitude),
                        end_time = COALESCE(end_time, :end_time),
                        image_url = COALESCE(image_url, :image_url),
                        scraped_at = CASE
                            WHEN scraped_at < :scraped_at THEN :scraped_at
                            ELSE scraped_at
                        END,
                        tags = COALESCE(tags, :tags),
                        score_breakdown = COALESCE(score_breakdown, :score_breakdown),
                        attended = CASE WHEN attended = 1 OR :attended = 1 THEN 1 ELSE 0 END
                    WHERE id = :canonical_id
                    """,
                    params,
                )
                await self.db.execute("DELETE FROM events WHERE id = :id", {"id": event.id})
                merged += 1
                if DEDUP_DEBUG:
                    logger.info(
                        "dedupe_backfill: merged id=%s into canonical=%s title=%s",
                        event.id,
                        duplicate_of.id,
                        event.title,
                    )

        await self.db.commit()
        return {"total_scanned": total, "merged": merged, "remaining": total - merged}


Database = SqliteDatabase | PostgresDatabase


def create_database(
    db_path: str | None = None, database_url: str | None = None
) -> SqliteDatabase | PostgresDatabase:
    """Database factory used by the rest of the app."""
    if db_path is not None and database_url is None:
        return SqliteDatabase(db_path=db_path, database_url=f"sqlite+aiosqlite:///{db_path}")

    resolved_url = database_url or settings.database_url
    if resolved_url.startswith("sqlite+aiosqlite:///"):
        return SqliteDatabase(db_path=db_path, database_url=resolved_url)
    if resolved_url.startswith("postgresql+"):
        return PostgresDatabase(database_url=resolved_url)
    raise ValueError(f"Unsupported DATABASE_URL scheme: {resolved_url}")
