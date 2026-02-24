"""Async SQLite database layer using aiosqlite."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite

from .models import Event, EventTags, Source

DATABASE_PATH = os.environ.get("DATABASE_PATH", "family_events.db")

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

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_events_start_time ON events(start_time);",
    "CREATE INDEX IF NOT EXISTS idx_events_source ON events(source, source_id);",
    "CREATE INDEX IF NOT EXISTS idx_events_city ON events(location_city);",
    "CREATE INDEX IF NOT EXISTS idx_events_tags ON events(tags) WHERE tags IS NULL;",
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
    # Datetimes stored as ISO strings
    d["start_time"] = datetime.fromisoformat(str(d["start_time"]))
    d["end_time"] = datetime.fromisoformat(str(d["end_time"])) if d["end_time"] else None
    d["scraped_at"] = datetime.fromisoformat(str(d["scraped_at"]))
    return Event.model_validate(d)


def _row_to_source(row: aiosqlite.Row) -> Source:
    """Convert a database row to a Source model."""
    d = dict(row)
    d["builtin"] = bool(d["builtin"])
    d["enabled"] = bool(d["enabled"])
    d["last_scraped_at"] = (
        datetime.fromisoformat(str(d["last_scraped_at"])) if d["last_scraped_at"] else None
    )
    d["created_at"] = datetime.fromisoformat(str(d["created_at"]))
    d["updated_at"] = datetime.fromisoformat(str(d["updated_at"]))
    return Source.model_validate(d)


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
        "attended": int(event.attended),
    }


class Database:
    """Async SQLite database for family events."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or DATABASE_PATH
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Open the database connection, enable WAL mode, and create tables."""
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._db.execute("PRAGMA foreign_keys=ON;")
        await self._db.execute(_CREATE_EVENTS_TABLE)
        await self._db.execute(_CREATE_SOURCES_TABLE)
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

        Returns the event id (existing id if already present).
        """
        params = _event_to_params(event)

        await self.db.execute(
            """
            INSERT INTO events (
                id, source, source_url, source_id, title, description,
                location_name, location_address, location_city,
                latitude, longitude, start_time, end_time,
                is_recurring, recurrence_rule, is_free,
                price_min, price_max, image_url,
                scraped_at, raw_data, tags, attended
            ) VALUES (
                :id, :source, :source_url, :source_id, :title, :description,
                :location_name, :location_address, :location_city,
                :latitude, :longitude, :start_time, :end_time,
                :is_recurring, :recurrence_rule, :is_free,
                :price_min, :price_max, :image_url,
                :scraped_at, :raw_data, :tags, :attended
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
            return row["id"]  # type: ignore[index]

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

    async def get_untagged_events(self) -> list[Event]:
        """Return events that have no AI-generated tags yet."""
        async with self.db.execute(
            "SELECT * FROM events WHERE tags IS NULL ORDER BY start_time"
        ) as cursor:
            rows = await cursor.fetchall()
            return [_row_to_event(r) for r in rows]

    async def update_event_tags(self, event_id: str, tags: EventTags) -> None:
        """Set the tags JSON for a specific event."""
        await self.db.execute(
            "UPDATE events SET tags = :tags WHERE id = :id",
            {"tags": json.dumps(tags.model_dump()), "id": event_id},
        )
        await self.db.commit()

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

    async def search_events(
        self,
        *,
        days: int = 30,
        q: str = "",
        city: str = "",
        source: str = "",
        tagged: str = "",
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
                id, name, url, domain, builtin, recipe_json,
                enabled, status, last_scraped_at, last_event_count,
                last_error, created_at, updated_at
            ) VALUES (
                :id, :name, :url, :domain, :builtin, :recipe_json,
                :enabled, :status, :last_scraped_at, :last_event_count,
                :last_error, :created_at, :updated_at
            )
            """,
            {
                "id": source.id,
                "name": source.name,
                "url": source.url,
                "domain": source.domain,
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
        await self.db.execute(
            "UPDATE events SET attended = 1 WHERE id = :id",
            {"id": event_id},
        )
        await self.db.commit()

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    async def __aenter__(self) -> Database:
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()
