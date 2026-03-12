"""Async SQLite database layer using aiosqlite."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite

from src.cities import normalize_city_slug
from src.config import settings
from src.db.common import (
    USER_UPDATE_FIELDS,
    canonicalize_title,
    event_fingerprint,
    normalize_email,
    normalize_search_query,
    time_window,
    title_similarity,
)
from src.db.postgres import PostgresDatabase
from src.timezones import as_local_date, utc_now, weekend_window_utc

from .models import Event, EventTags, InterestProfile, Job, Source, User, UserEventState

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
    city_slug       TEXT NOT NULL DEFAULT 'lafayette',
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
    city_slug       TEXT NOT NULL DEFAULT 'unknown',
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

_CREATE_USER_EVENT_STATE_TABLE = """
CREATE TABLE IF NOT EXISTS user_event_state (
    user_id         TEXT NOT NULL,
    event_id        TEXT NOT NULL,
    saved           INTEGER NOT NULL DEFAULT 0,
    attended        INTEGER NOT NULL DEFAULT 0,
    saved_at        TEXT,
    attended_at     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE(user_id, event_id),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE
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
    "CREATE INDEX IF NOT EXISTS idx_events_city ON events(city_slug);",
    "CREATE INDEX IF NOT EXISTS idx_events_tags ON events(tags) WHERE tags IS NULL;",
    "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);",
    "CREATE INDEX IF NOT EXISTS idx_sources_user_id ON sources(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_user_event_state_flags ON user_event_state(user_id, saved, attended);",
    "CREATE INDEX IF NOT EXISTS idx_user_event_state_updated ON user_event_state(user_id, updated_at DESC);",
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
    viewer_saved = d.pop("viewer_saved", None)
    viewer_attended = d.pop("viewer_attended", None)
    if viewer_saved is not None or viewer_attended is not None:
        d["viewer_state"] = UserEventState(
            saved=bool(viewer_saved or 0),
            attended=bool(viewer_attended or 0),
        )
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
    d["city_slug"] = str(d.get("city_slug") or "unknown")
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
        "city_slug": event.city_slug,
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
    }


def _sqlite_path_from_url(database_url: str) -> str | None:
    prefix = "sqlite+aiosqlite:///"
    if database_url.startswith(prefix):
        return database_url.removeprefix(prefix)
    return None


def _event_query_parts(viewer_user_id: str | None) -> tuple[str, str, dict[str, Any]]:
    if not viewer_user_id:
        return "e.*", "", {}
    return (
        "e.*, COALESCE(ues.saved, 0) AS viewer_saved, COALESCE(ues.attended, 0) AS viewer_attended",
        "LEFT JOIN user_event_state ues ON ues.event_id = e.id AND ues.user_id = :viewer_user_id",
        {"viewer_user_id": viewer_user_id},
    )


def _add_city_slug_filter(
    conditions: list[str],
    params: dict[str, Any],
    visible_city_slugs: list[str] | None,
    *,
    column: str = "e.city_slug",
) -> None:
    if not visible_city_slugs:
        return
    placeholders: list[str] = []
    for index, city_slug in enumerate(visible_city_slugs):
        key = f"visible_city_slug_{index}"
        params[key] = city_slug
        placeholders.append(f":{key}")
    conditions.append(f"{column} IN ({', '.join(placeholders)})")


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
        await self._db.execute(_CREATE_USER_EVENT_STATE_TABLE)
        await self._db.execute(_CREATE_JOBS_TABLE)
        # Migrations for existing databases
        for migration in [
            "ALTER TABLE sources ADD COLUMN user_id TEXT",
            "ALTER TABLE sources ADD COLUMN city TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE sources ADD COLUMN city_slug TEXT NOT NULL DEFAULT 'unknown'",
            "ALTER TABLE sources ADD COLUMN category TEXT NOT NULL DEFAULT 'custom'",
            "ALTER TABLE users ADD COLUMN email_to TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE users ADD COLUMN sms_to TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE users ADD COLUMN child_name TEXT NOT NULL DEFAULT 'Your Little One'",
            "ALTER TABLE users ADD COLUMN onboarding_complete INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE events ADD COLUMN tagged_at TEXT",
            "ALTER TABLE events ADD COLUMN score_breakdown TEXT",
            "ALTER TABLE events ADD COLUMN city_slug TEXT NOT NULL DEFAULT 'lafayette'",
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

    async def health_stats(self) -> dict[str, Any]:
        """Return basic health/freshness stats for the service."""
        cutoff = (
            datetime.now(tz=UTC) - timedelta(seconds=settings.background_job_timeout_seconds)
        ).isoformat()
        async with self.db.execute(
            """
            SELECT
                COUNT(*) AS event_count,
                MAX(scraped_at) AS latest_scraped_at,
                MAX(tagged_at) AS latest_tagged_at
            FROM events
            """
        ) as cursor:
            event_row = await cursor.fetchone()
        async with self.db.execute(
            """
            SELECT MAX(finished_at) AS latest_notified_at
            FROM jobs
            WHERE kind = 'notify' AND state = 'succeeded'
            """
        ) as cursor:
            notify_row = await cursor.fetchone()
        async with self.db.execute(
            """
            SELECT COUNT(*) AS stuck_running_jobs
            FROM jobs
            WHERE state = 'running' AND COALESCE(started_at, created_at) < :cutoff
            """,
            {"cutoff": cutoff},
        ) as cursor:
            stuck_row = await cursor.fetchone()

        latest_scraped_at = event_row["latest_scraped_at"] if event_row else None
        latest_tagged_at = event_row["latest_tagged_at"] if event_row else None
        latest_notified_at = notify_row["latest_notified_at"] if notify_row else None
        return {
            "event_count": int(event_row["event_count"])
            if event_row and event_row["event_count"] is not None
            else 0,
            "latest_scraped_at": datetime.fromisoformat(str(latest_scraped_at))
            if latest_scraped_at
            else None,
            "latest_tagged_at": datetime.fromisoformat(str(latest_tagged_at))
            if latest_tagged_at
            else None,
            "latest_notified_at": datetime.fromisoformat(str(latest_notified_at))
            if latest_notified_at
            else None,
            "stuck_running_jobs": int(stuck_row["stuck_running_jobs"])
            if stuck_row and stuck_row["stuck_running_jobs"] is not None
            else 0,
        }

    # ------------------------------------------------------------------
    # Upsert
    # ------------------------------------------------------------------

    async def upsert_event(self, event: Event) -> str:
        """Insert or update an event, keyed on (source, source_id).

        Also applies cross-source dedupe using fuzzy match on title/date/city.
        Returns the canonical event id (existing id if deduped).
        """
        event.location_city = (event.location_city or "").strip()
        if not event.location_city:
            event.location_city = await self._fallback_event_city(event)
        event.city_slug = normalize_city_slug(event.location_city)

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
                        city_slug = :city_slug,
                        location_city = CASE
                            WHEN (location_city IS NULL OR location_city = '' OR location_city = 'unknown') AND :location_city != '' THEN :location_city
                            ELSE location_city
                        END
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
                location_name, location_address, location_city, city_slug,
                latitude, longitude, start_time, end_time,
                is_recurring, recurrence_rule, is_free,
                price_min, price_max, image_url,
                scraped_at, raw_data, tags, score_breakdown
            ) VALUES (
                :id, :source, :source_url, :source_id, :title, :description,
                :location_name, :location_address, :location_city, :city_slug,
                :latitude, :longitude, :start_time, :end_time,
                :is_recurring, :recurrence_rule, :is_free,
                :price_min, :price_max, :image_url,
                :scraped_at, :raw_data, :tags, :score_breakdown
            )
            ON CONFLICT(source, source_id) DO UPDATE SET
                source_url      = excluded.source_url,
                title           = excluded.title,
                description     = excluded.description,
                location_name   = excluded.location_name,
                location_address = excluded.location_address,
                location_city   = excluded.location_city,
                city_slug       = excluded.city_slug,
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

    async def _fallback_event_city(self, event: Event) -> str:
        source_id = ""
        if event.source.startswith("custom:"):
            source_id = event.source.removeprefix("custom:")
        elif event.raw_data.get("source_id"):
            source_id = str(event.raw_data.get("source_id"))

        if source_id:
            async with self.db.execute(
                "SELECT city FROM sources WHERE id = :id", {"id": source_id}
            ) as cursor:
                row = await cursor.fetchone()
                if row and str(row["city"] or "").strip():
                    return str(row["city"]).strip()

        async with self.db.execute(
            "SELECT city FROM sources WHERE url = :url",
            {"url": event.source_url},
        ) as cursor:
            row = await cursor.fetchone()
            if row and str(row["city"] or "").strip():
                return str(row["city"]).strip()

        return "unknown"

    async def _find_duplicate_event_id(self, event: Event) -> tuple[str | None, str | None]:
        """Find a likely duplicate event across sources.

        Returns (event_id, reason).
        """
        start = event.start_time - timedelta(hours=4)
        end = event.start_time + timedelta(hours=4)
        async with self.db.execute(
            """
            SELECT id, title, source, source_id, start_time, city_slug
            FROM events
            WHERE city_slug = :city_slug
              AND start_time >= :start
              AND start_time <= :end
            """,
            {
                "city_slug": event.city_slug,
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
        ) as cursor:
            rows = await cursor.fetchall()

        fp = event_fingerprint(event)
        for row in rows:
            candidate_fp = hashlib.sha1(
                (
                    f"{canonicalize_title(str(row['title']))}|"
                    f"{as_local_date(datetime.fromisoformat(str(row['start_time']))).isoformat()}|"
                    f"{str(row['city_slug']).strip()}"
                ).encode()
            ).hexdigest()
            if candidate_fp == fp:
                return str(row["id"]), "fingerprint"

        for row in rows:
            similarity = title_similarity(event.title, str(row["title"]))
            if similarity >= 0.75:
                return str(row["id"]), f"title_similarity:{similarity:.2f}"

        return None, None

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get_events_for_weekend(
        self,
        sat_date: str,
        sun_date: str,
        *,
        viewer_user_id: str | None = None,
        visible_city_slugs: list[str] | None = None,
        attended: str = "",
        saved: str = "",
    ) -> list[Event]:
        """Return events whose local start date falls on the given Saturday or Sunday."""
        saturday = datetime.fromisoformat(sat_date).date()
        sunday = datetime.fromisoformat(sun_date).date()
        start, end = weekend_window_utc(saturday, sunday)
        select_cols, join_sql, extra_params = _event_query_parts(viewer_user_id)
        conditions = ["e.start_time >= :start", "e.start_time < :end"]
        params: dict[str, Any] = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            **extra_params,
        }
        _add_city_slug_filter(conditions, params, visible_city_slugs)
        if viewer_user_id:
            if attended == "yes":
                conditions.append("COALESCE(ues.attended, 0) = 1")
            elif attended == "no":
                conditions.append("COALESCE(ues.attended, 0) = 0")
            if saved == "yes":
                conditions.append("COALESCE(ues.saved, 0) = 1")
            elif saved == "no":
                conditions.append("COALESCE(ues.saved, 0) = 0")
        async with self.db.execute(
            f"""
            SELECT {select_cols}
            FROM events e
            {join_sql}
            WHERE {" AND ".join(conditions)}
            ORDER BY e.start_time
            """,
            params,
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
        now = utc_now().isoformat()
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

    async def get_recent_events(
        self,
        days: int = 14,
        *,
        viewer_user_id: str | None = None,
        visible_city_slugs: list[str] | None = None,
    ) -> list[Event]:
        """Return events with start_time within the next `days` days."""
        now_dt, future_dt = time_window(days)
        select_cols, join_sql, extra_params = _event_query_parts(viewer_user_id)
        conditions = ["e.start_time >= :now", "e.start_time <= :future"]
        params: dict[str, Any] = {
            "now": now_dt.isoformat(),
            "future": future_dt.isoformat(),
            **extra_params,
        }
        _add_city_slug_filter(conditions, params, visible_city_slugs)
        async with self.db.execute(
            f"""
            SELECT {select_cols}
            FROM events e
            {join_sql}
            WHERE {" AND ".join(conditions)}
            ORDER BY e.start_time
            """,
            params,
        ) as cursor:
            rows = await cursor.fetchall()
            return [_row_to_event(r) for r in rows]

    async def get_events_between(
        self,
        start: datetime,
        end: datetime,
        *,
        viewer_user_id: str | None = None,
        visible_city_slugs: list[str] | None = None,
        attended: str = "",
        saved: str = "",
    ) -> list[Event]:
        """Return events in [start, end), optionally filtering attended status."""
        select_cols, join_sql, extra_params = _event_query_parts(viewer_user_id)
        conditions = ["e.start_time >= :start", "e.start_time < :end"]
        params: dict[str, Any] = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            **extra_params,
        }
        _add_city_slug_filter(conditions, params, visible_city_slugs)

        if viewer_user_id:
            if attended == "yes":
                conditions.append("COALESCE(ues.attended, 0) = 1")
            elif attended == "no":
                conditions.append("COALESCE(ues.attended, 0) = 0")
            if saved == "yes":
                conditions.append("COALESCE(ues.saved, 0) = 1")
            elif saved == "no":
                conditions.append("COALESCE(ues.saved, 0) = 0")

        where = " AND ".join(conditions)
        async with self.db.execute(
            f"""
            SELECT {select_cols}
            FROM events e
            {join_sql}
            WHERE {where}
            ORDER BY e.start_time
            """,
            params,
        ) as cursor:
            rows = await cursor.fetchall()
            return [_row_to_event(r) for r in rows]

    async def search_events(
        self,
        *,
        days: int = 30,
        viewer_user_id: str | None = None,
        visible_city_slugs: list[str] | None = None,
        q: str = "",
        city: str = "",
        source: str = "",
        tagged: str = "",
        attended: str = "",
        saved: str = "",
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
        now_dt, future_dt = time_window(days)

        select_cols, join_sql, extra_params = _event_query_parts(viewer_user_id)
        conditions = ["e.start_time >= :now", "e.start_time <= :future"]
        params: dict[str, Any] = {
            "now": now_dt.isoformat(),
            "future": future_dt.isoformat(),
            **extra_params,
        }
        _add_city_slug_filter(conditions, params, visible_city_slugs)

        if q:
            conditions.append("(e.title LIKE :q OR e.description LIKE :q)")
            params["q"] = f"%{q}%"

        if city:
            conditions.append("e.city_slug = :city_slug")
            params["city_slug"] = normalize_city_slug(city)

        if source:
            conditions.append("e.source = :source")
            params["source"] = source

        if tagged == "yes":
            conditions.append("e.tags IS NOT NULL")
        elif tagged == "no":
            conditions.append("e.tags IS NULL")

        if viewer_user_id:
            if attended == "yes":
                conditions.append("COALESCE(ues.attended, 0) = 1")
            elif attended == "no":
                conditions.append("COALESCE(ues.attended, 0) = 0")
            if saved == "yes":
                conditions.append("COALESCE(ues.saved, 0) = 1")
            elif saved == "no":
                conditions.append("COALESCE(ues.saved, 0) = 0")

        if score_min is not None:
            conditions.append(
                "e.tags IS NOT NULL AND CAST(json_extract(e.tags, '$.toddler_score') AS INTEGER) >= :score_min"
            )
            params["score_min"] = score_min

        where = " AND ".join(conditions)

        # Count total
        count_sql = f"SELECT COUNT(*) FROM events e {join_sql} WHERE {where}"
        async with self.db.execute(count_sql, params) as cursor:
            row = await cursor.fetchone()
            total = row[0] if row else 0

        # Determine sort
        _valid_sorts = {
            "start_time": "e.start_time",
            "-start_time": "e.start_time DESC",
            "title": "e.title",
            "-title": "e.title DESC",
            "city": "e.location_city",
            "-city": "e.location_city DESC",
            "source": "e.source",
            "-source": "e.source DESC",
            "score": "CAST(json_extract(e.tags, '$.toddler_score') AS INTEGER)",
            "-score": "CAST(json_extract(e.tags, '$.toddler_score') AS INTEGER) DESC",
        }
        order_clause = _valid_sorts.get(sort, "e.start_time")

        offset = (page - 1) * per_page
        params["limit"] = per_page
        params["offset"] = offset

        query_sql = f"""
            SELECT {select_cols}
            FROM events e
            {join_sql}
            WHERE {where}
            ORDER BY {order_clause}
            LIMIT :limit OFFSET :offset
        """
        async with self.db.execute(query_sql, params) as cursor:
            rows = await cursor.fetchall()
            events = [_row_to_event(r) for r in rows]

        return events, int(total)

    async def get_filter_options(
        self,
        *,
        visible_city_slugs: list[str] | None = None,
    ) -> dict[str, list[str]]:
        """Return distinct values for filter dropdowns."""
        city_conditions: list[str] = []
        city_params: dict[str, Any] = {}
        _add_city_slug_filter(city_conditions, city_params, visible_city_slugs, column="city_slug")
        city_where = f"WHERE {' AND '.join(city_conditions)}" if city_conditions else ""
        cities: list[str] = []
        async with self.db.execute(
            f"""
            SELECT MIN(location_city) AS location_city
            FROM events
            {city_where}
            GROUP BY city_slug
            ORDER BY location_city
            """,
            city_params,
        ) as cursor:
            cities = [row[0] for row in await cursor.fetchall() if row[0]]

        sources: list[str] = []
        async with self.db.execute(
            f"SELECT DISTINCT source FROM events {city_where} ORDER BY source",
            city_params,
        ) as cursor:
            sources = [row[0] for row in await cursor.fetchall()]

        return {"cities": cities, "sources": sources}

    # ------------------------------------------------------------------
    # Sources CRUD
    # ------------------------------------------------------------------

    async def create_source(self, source: Source) -> str:
        """Insert a new source. Returns the source id."""
        source.city_slug = normalize_city_slug(source.city)
        await self.db.execute(
            """
            INSERT INTO sources (
                id, name, url, domain, city, city_slug, category, user_id, builtin, recipe_json,
                enabled, status, last_scraped_at, last_event_count,
                last_error, created_at, updated_at
            ) VALUES (
                :id, :name, :url, :domain, :city, :city_slug, :category, :user_id, :builtin, :recipe_json,
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
                "city_slug": source.city_slug,
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

    async def get_event(self, event_id: str, *, viewer_user_id: str | None = None) -> Event | None:
        """Get a single event by id."""
        select_cols, join_sql, params = _event_query_parts(viewer_user_id)
        params["id"] = event_id
        async with self.db.execute(
            f"SELECT {select_cols} FROM events e {join_sql} WHERE e.id = :id",
            params,
        ) as cursor:
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
        now = utc_now().isoformat()
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
        now = utc_now().isoformat()
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
        now = utc_now().isoformat()
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

    async def get_or_create_user_event_state(self, user_id: str, event_id: str) -> UserEventState:
        now = utc_now().isoformat()
        await self.db.execute(
            """
            INSERT INTO user_event_state (
                user_id, event_id, saved, attended, saved_at, attended_at, created_at, updated_at
            ) VALUES (
                :user_id, :event_id, 0, 0, NULL, NULL, :now, :now
            )
            ON CONFLICT(user_id, event_id) DO NOTHING
            """,
            {"user_id": user_id, "event_id": event_id, "now": now},
        )
        await self.db.commit()
        async with self.db.execute(
            """
            SELECT saved, attended
            FROM user_event_state
            WHERE user_id = :user_id AND event_id = :event_id
            """,
            {"user_id": user_id, "event_id": event_id},
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return UserEventState()
            return UserEventState(saved=bool(row["saved"]), attended=bool(row["attended"]))

    async def set_event_saved(self, user_id: str, event_id: str, saved: bool) -> None:
        now = utc_now().isoformat()
        await self.db.execute(
            """
            INSERT INTO user_event_state (
                user_id, event_id, saved, attended, saved_at, attended_at, created_at, updated_at
            ) VALUES (
                :user_id, :event_id, :saved, 0, :saved_at, NULL, :now, :now
            )
            ON CONFLICT(user_id, event_id) DO UPDATE SET
                saved = :saved,
                saved_at = :saved_at,
                updated_at = :now
            """,
            {
                "user_id": user_id,
                "event_id": event_id,
                "saved": int(saved),
                "saved_at": now if saved else None,
                "now": now,
            },
        )
        await self.db.commit()

    async def set_event_attended(self, user_id: str, event_id: str, attended: bool) -> None:
        now = utc_now().isoformat()
        await self.db.execute(
            """
            INSERT INTO user_event_state (
                user_id, event_id, saved, attended, saved_at, attended_at, created_at, updated_at
            ) VALUES (
                :user_id, :event_id, 0, :attended, NULL, :attended_at, :now, :now
            )
            ON CONFLICT(user_id, event_id) DO UPDATE SET
                attended = :attended,
                attended_at = :attended_at,
                updated_at = :now
            """,
            {
                "user_id": user_id,
                "event_id": event_id,
                "attended": int(attended),
                "attended_at": now if attended else None,
                "now": now,
            },
        )
        await self.db.commit()

    async def set_event_attended_bulk(
        self, user_id: str, event_ids: list[str], attended: bool
    ) -> None:
        if not event_ids:
            return
        now = utc_now().isoformat()
        await self.db.executemany(
            """
            INSERT INTO user_event_state (
                user_id, event_id, saved, attended, saved_at, attended_at, created_at, updated_at
            ) VALUES (?, ?, 0, ?, NULL, ?, ?, ?)
            ON CONFLICT(user_id, event_id) DO UPDATE SET
                attended = excluded.attended,
                attended_at = excluded.attended_at,
                updated_at = excluded.updated_at
            """,
            [
                (
                    user_id,
                    event_id,
                    int(attended),
                    now if attended else None,
                    now,
                    now,
                )
                for event_id in event_ids
            ],
        )
        await self.db.commit()

    async def list_my_events(
        self,
        *,
        viewer_user_id: str,
        q: str = "",
        city: str = "",
        source: str = "",
        tagged: str = "",
        attended: str = "",
        saved: str = "",
        sort: str = "-start_time",
        page: int = 1,
        per_page: int = 25,
    ) -> tuple[list[Event], int]:
        select_cols, join_sql, extra_params = _event_query_parts(viewer_user_id)
        conditions = ["(COALESCE(ues.saved, 0) = 1 OR COALESCE(ues.attended, 0) = 1)"]
        params: dict[str, Any] = dict(extra_params)

        if q:
            conditions.append("(e.title LIKE :q OR e.description LIKE :q)")
            params["q"] = f"%{q}%"
        if city:
            conditions.append("e.city_slug = :city_slug")
            params["city_slug"] = normalize_city_slug(city)
        if source:
            conditions.append("e.source = :source")
            params["source"] = source
        if tagged == "yes":
            conditions.append("e.tags IS NOT NULL")
        elif tagged == "no":
            conditions.append("e.tags IS NULL")
        if attended == "yes":
            conditions.append("COALESCE(ues.attended, 0) = 1")
        elif attended == "no":
            conditions.append("COALESCE(ues.attended, 0) = 0")
        if saved == "yes":
            conditions.append("COALESCE(ues.saved, 0) = 1")
        elif saved == "no":
            conditions.append("COALESCE(ues.saved, 0) = 0")

        where = " AND ".join(conditions)
        valid_sorts = {
            "start_time": "e.start_time",
            "-start_time": "e.start_time DESC",
            "title": "e.title",
            "-title": "e.title DESC",
            "city": "e.location_city",
            "-city": "e.location_city DESC",
            "source": "e.source",
            "-source": "e.source DESC",
            "score": "CAST(json_extract(e.tags, '$.toddler_score') AS INTEGER)",
            "-score": "CAST(json_extract(e.tags, '$.toddler_score') AS INTEGER) DESC",
        }
        order_clause = valid_sorts.get(sort, "e.start_time DESC")
        params["limit"] = per_page
        params["offset"] = (page - 1) * per_page

        async with self.db.execute(
            f"SELECT COUNT(*) FROM events e {join_sql} WHERE {where}",
            params,
        ) as cursor:
            row = await cursor.fetchone()
            total = int(row[0] if row else 0)

        async with self.db.execute(
            f"""
            SELECT {select_cols}
            FROM events e
            {join_sql}
            WHERE {where}
            ORDER BY {order_clause}
            LIMIT :limit OFFSET :offset
            """,
            params,
        ) as cursor:
            rows = await cursor.fetchall()
            return [_row_to_event(row) for row in rows], total

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
        owner_user_id: str | None,
        source_id: str | None = None,
        state: str | None = None,
        kind: str | None = None,
        q: str = "",
        limit: int = 20,
    ) -> list[Job]:
        """List recent jobs with optional user/source filters."""
        params: dict[str, Any] = {"limit": limit}
        if owner_user_id is None:
            sql = "SELECT * FROM jobs WHERE owner_user_id IS NULL"
        else:
            sql = "SELECT * FROM jobs WHERE owner_user_id = :owner_user_id"
            params["owner_user_id"] = owner_user_id
        if source_id is not None:
            sql += " AND source_id = :source_id"
            params["source_id"] = source_id
        if state:
            sql += " AND state = :state"
            params["state"] = state
        if kind:
            sql += " AND kind = :kind"
            params["kind"] = kind
        q = normalize_search_query(q)
        if q:
            sql += " AND (label LIKE :q OR detail LIKE :q OR error LIKE :q)"
            params["q"] = f"%{q}%"
        sql += " ORDER BY created_at DESC LIMIT :limit"
        async with self.db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
            return [_row_to_job(r) for r in rows]

    async def list_job_kinds(self, *, owner_user_id: str | None) -> list[str]:
        """List distinct job kinds for a user or system jobs."""
        if owner_user_id is None:
            sql = "SELECT DISTINCT kind FROM jobs WHERE owner_user_id IS NULL ORDER BY kind ASC"
            params: dict[str, Any] = {}
        else:
            sql = "SELECT DISTINCT kind FROM jobs WHERE owner_user_id = :owner_user_id ORDER BY kind ASC"
            params = {"owner_user_id": owner_user_id}
        async with self.db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
            return [str(row["kind"]) for row in rows if row["kind"]]

    async def fail_stale_jobs(self, *, max_age_seconds: int) -> int:
        """Mark long-running jobs failed so stale records stop blocking new work."""
        now_dt = utc_now()
        now = now_dt.isoformat()
        cutoff = (now_dt - timedelta(seconds=max_age_seconds)).isoformat()
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
            "SELECT * FROM users WHERE email = :email", {"email": normalize_email(email)}
        ) as cursor:
            row = await cursor.fetchone()
            return _row_to_user(row) if row else None

    async def update_user(self, user_id: str, **fields: Any) -> None:
        """Update specific fields on a user."""
        allowed = USER_UPDATE_FIELDS
        now = utc_now().isoformat()
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
                    fp_a = event_fingerprint(event)
                    fp_b = event_fingerprint(c)
                    sim = title_similarity(event.title, c.title)
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
                        city_slug = :city_slug,
                        location_city = CASE
                            WHEN (location_city IS NULL OR location_city = '' OR location_city = 'unknown') AND :location_city != '' THEN :location_city
                            ELSE location_city
                        END
                    WHERE id = :canonical_id
                    """,
                    params,
                )
                await self.db.execute(
                    """
                    INSERT INTO user_event_state (
                        user_id, event_id, saved, attended, saved_at, attended_at, created_at, updated_at
                    )
                    SELECT
                        user_id,
                        :canonical_id,
                        saved,
                        attended,
                        saved_at,
                        attended_at,
                        created_at,
                        updated_at
                    FROM user_event_state
                    WHERE event_id = :duplicate_id
                    ON CONFLICT(user_id, event_id) DO UPDATE SET
                        saved = CASE WHEN user_event_state.saved = 1 OR excluded.saved = 1 THEN 1 ELSE 0 END,
                        attended = CASE WHEN user_event_state.attended = 1 OR excluded.attended = 1 THEN 1 ELSE 0 END,
                        saved_at = COALESCE(user_event_state.saved_at, excluded.saved_at),
                        attended_at = COALESCE(user_event_state.attended_at, excluded.attended_at),
                        updated_at = CASE
                            WHEN user_event_state.updated_at > excluded.updated_at THEN user_event_state.updated_at
                            ELSE excluded.updated_at
                        END
                    """,
                    {"canonical_id": duplicate_of.id, "duplicate_id": event.id},
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
