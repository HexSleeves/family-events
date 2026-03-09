"""Postgres-backed database implementation."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.db.common import (
    USER_UPDATE_FIELDS,
    canonicalize_title,
    event_fingerprint,
    normalize_email,
    normalize_search_query,
    time_window,
    title_similarity,
)
from src.db.models import Event, EventTags, InterestProfile, Job, Source, User
from src.db.session import get_engine, get_sessionmaker

logger = logging.getLogger("uvicorn.error")


def _uuid_param(value: str | None) -> uuid.UUID | str | None:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return value



def _normalize_uuid(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return str(value)
    return str(value)


def _row_to_event(row: Any) -> Event:
    data = dict(row)
    data["id"] = _normalize_uuid(data.get("id"))
    tags = data.get("tags")
    data["tags"] = EventTags.model_validate(tags) if tags else None
    return Event.model_validate(data)


def _row_to_user(row: Any) -> User:
    data = dict(row)
    data["id"] = _normalize_uuid(data.get("id"))
    raw_profile = data.get("interest_profile") or {}
    data["interest_profile"] = (
        InterestProfile.model_validate(raw_profile) if raw_profile else InterestProfile()
    )
    return User.model_validate(data)


def _row_to_job(row: Any) -> Job:
    data = dict(row)
    data["id"] = _normalize_uuid(data.get("id"))
    data["owner_user_id"] = _normalize_uuid(data.get("owner_user_id"))
    data["source_id"] = _normalize_uuid(data.get("source_id"))
    return Job.model_validate(data)


def _row_to_source(row: Any) -> Source:
    data = dict(row)
    data["id"] = _normalize_uuid(data.get("id"))
    data["user_id"] = _normalize_uuid(data.get("user_id"))
    return Source.model_validate(data)


class PostgresDatabase:
    """Incremental Postgres implementation behind the existing DB API."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.engine: AsyncEngine | None = None
        self.sessionmaker: async_sessionmaker[AsyncSession] | None = None

    @property
    def db_path(self) -> str | None:
        return None

    async def connect(self) -> None:
        self.engine = get_engine(self.database_url)
        self.sessionmaker = get_sessionmaker(self.database_url)
        async with self.engine.connect() as conn:
            await conn.run_sync(lambda _sync_conn: None)

    async def close(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()
            self.engine = None
            self.sessionmaker = None

    @asynccontextmanager
    async def session(self):
        if self.sessionmaker is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        async with self.sessionmaker() as session:
            yield session

    async def health_stats(self) -> dict[str, Any]:
        async with self.session() as session:
            result = await session.execute(
                text("SELECT COUNT(*) AS n, MAX(scraped_at) AS latest FROM events")
            )
            row = result.mappings().first()
            return {
                "event_count": int(row["n"]) if row and row["n"] is not None else 0,
                "latest_scraped_at": row["latest"] if row else None,
            }

    async def upsert_event(self, event: Event) -> str:
        async with self.session() as session:
            existing_result = await session.execute(
                text("SELECT id FROM events WHERE source = :source AND source_id = :source_id"),
                {"source": event.source, "source_id": event.source_id},
            )
            existing = existing_result.mappings().first()
            if existing:
                event.id = _normalize_uuid(existing["id"]) or event.id

            if not existing:
                canonical_id, _dedupe_reason = await self._find_duplicate_event_id(event, session=session)
                if canonical_id:
                    await session.execute(
                        text(
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
                                tags = COALESCE(tags, CAST(:tags AS jsonb)),
                                score_breakdown = COALESCE(score_breakdown, CAST(:score_breakdown AS jsonb)),
                                attended = attended OR :attended
                            WHERE id = :canonical_id
                            """
                        ),
                        self._event_params(event) | {"canonical_id": canonical_id},
                    )
                    await session.commit()
                    return canonical_id

            await session.execute(
                text(
                    """
                    INSERT INTO events (
                        id, source, source_url, source_id, title, description,
                        location_name, location_address, location_city,
                        latitude, longitude, start_time, end_time,
                        is_recurring, recurrence_rule, is_free,
                        price_min, price_max, image_url,
                        scraped_at, raw_data, tags, tagged_at, score_breakdown, attended
                    ) VALUES (
                        :id, :source, :source_url, :source_id, :title, :description,
                        :location_name, :location_address, :location_city,
                        :latitude, :longitude, :start_time, :end_time,
                        :is_recurring, :recurrence_rule, :is_free,
                        :price_min, :price_max, :image_url,
                        :scraped_at, CAST(:raw_data AS jsonb), CAST(:tags AS jsonb), :tagged_at, CAST(:score_breakdown AS jsonb), :attended
                    )
                    ON CONFLICT (source, source_id) DO UPDATE SET
                        source_url = EXCLUDED.source_url,
                        title = EXCLUDED.title,
                        description = EXCLUDED.description,
                        location_name = EXCLUDED.location_name,
                        location_address = EXCLUDED.location_address,
                        location_city = EXCLUDED.location_city,
                        latitude = EXCLUDED.latitude,
                        longitude = EXCLUDED.longitude,
                        start_time = EXCLUDED.start_time,
                        end_time = EXCLUDED.end_time,
                        is_recurring = EXCLUDED.is_recurring,
                        recurrence_rule = EXCLUDED.recurrence_rule,
                        is_free = EXCLUDED.is_free,
                        price_min = EXCLUDED.price_min,
                        price_max = EXCLUDED.price_max,
                        image_url = EXCLUDED.image_url,
                        scraped_at = EXCLUDED.scraped_at,
                        raw_data = EXCLUDED.raw_data
                    RETURNING id
                    """
                ),
                self._event_params(event),
            )
            await session.commit()
            result = await session.execute(
                text("SELECT id FROM events WHERE source = :source AND source_id = :source_id"),
                {"source": event.source, "source_id": event.source_id},
            )
            row = result.mappings().first()
            return _normalize_uuid(row["id"]) if row else event.id

    def _event_params(self, event: Event) -> dict[str, Any]:
        return {
            "id": _uuid_param(event.id),
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
            "start_time": event.start_time,
            "end_time": event.end_time,
            "is_recurring": event.is_recurring,
            "recurrence_rule": event.recurrence_rule,
            "is_free": event.is_free,
            "price_min": event.price_min,
            "price_max": event.price_max,
            "image_url": event.image_url,
            "scraped_at": event.scraped_at,
            "raw_data": json.dumps(event.raw_data),
            "tags": json.dumps(event.tags.model_dump()) if event.tags else None,
            "tagged_at": None,
            "score_breakdown": json.dumps(event.score_breakdown) if event.score_breakdown else None,
            "attended": event.attended,
        }

    async def _find_duplicate_event_id(
        self,
        event: Event,
        *,
        session: AsyncSession | None = None,
    ) -> tuple[str | None, str | None]:
        own_session = session is None
        if own_session:
            session_cm = self.session()
            session = await session_cm.__aenter__()
        assert session is not None
        try:
            start = event.start_time - timedelta(hours=4)
            end = event.start_time + timedelta(hours=4)
            result = await session.execute(
                text(
                    """
                    SELECT id, title, source, source_id, start_time, location_city
                    FROM events
                    WHERE location_city = :city
                      AND start_time >= :start
                      AND start_time <= :end
                    """
                ),
                {"city": event.location_city, "start": start, "end": end},
            )
            rows = result.mappings().all()
            fp = event_fingerprint(event)
            for row in rows:
                candidate_fp = hashlib.sha1(
                    (
                        f"{canonicalize_title(str(row['title']))}|"
                        f"{row['start_time'].date().isoformat()}|"
                        f"{str(row['location_city']).lower().strip()}"
                    ).encode()
                ).hexdigest()
                if candidate_fp == fp:
                    return _normalize_uuid(row["id"]), "fingerprint"
            for row in rows:
                similarity = title_similarity(event.title, str(row["title"]))
                if similarity >= 0.75:
                    return _normalize_uuid(row["id"]), f"title_similarity:{similarity:.2f}"
            return None, None
        finally:
            if own_session:
                await session_cm.__aexit__(None, None, None)

    async def get_events_for_weekend(self, sat_date: str, sun_date: str) -> list[Event]:
        sat_start = datetime.fromisoformat(f"{sat_date}T00:00:00+00:00")
        mon_start = datetime.fromisoformat(f"{sun_date}T23:59:59+00:00")
        async with self.session() as session:
            result = await session.execute(
                text(
                    "SELECT * FROM events WHERE start_time >= :sat_start AND start_time <= :mon_start ORDER BY start_time"
                ),
                {"sat_start": sat_start, "mon_start": mon_start},
            )
            return [_row_to_event(row) for row in result.mappings().all()]

    async def get_untagged_events(
        self,
        *,
        tagging_version: str | None = None,
        include_stale: bool = True,
    ) -> list[Event]:
        if tagging_version is None or not include_stale:
            sql = "SELECT * FROM events WHERE tags IS NULL ORDER BY start_time"
            params: dict[str, Any] = {}
        else:
            sql = (
                "SELECT * FROM events WHERE tags IS NULL "
                "OR COALESCE(tags->>'tagging_version', '') != :tagging_version ORDER BY start_time"
            )
            params = {"tagging_version": tagging_version}
        async with self.session() as session:
            result = await session.execute(text(sql), params)
            return [_row_to_event(row) for row in result.mappings().all()]

    async def count_stale_tagged_events(self, *, tagging_version: str) -> int:
        async with self.session() as session:
            result = await session.execute(
                text(
                    "SELECT COUNT(*) FROM events WHERE tags IS NOT NULL AND COALESCE(tags->>'tagging_version', '') != :tagging_version"
                ),
                {"tagging_version": tagging_version},
            )
            row = result.first()
            return int(row[0] if row else 0)

    async def update_event_tags(
        self,
        event_id: str,
        tags: EventTags,
        *,
        score_breakdown: dict[str, float] | None = None,
    ) -> None:
        async with self.session() as session:
            await session.execute(
                text(
                    "UPDATE events SET tags = CAST(:tags AS jsonb), score_breakdown = CAST(:score_breakdown AS jsonb), tagged_at = :tagged_at WHERE id = :id"
                ),
                {
                    "tags": json.dumps(tags.model_dump()),
                    "score_breakdown": json.dumps(score_breakdown) if score_breakdown else None,
                    "tagged_at": datetime.now(tz=UTC),
                    "id": _uuid_param(event_id),
                },
            )
            await session.commit()

    async def get_pipeline_timestamps(self) -> dict[str, datetime | None]:
        async with self.session() as session:
            result = await session.execute(
                text("SELECT MAX(scraped_at) AS last_scraped_at, MAX(tagged_at) AS last_tagged_at FROM events")
            )
            row = result.mappings().first()
            return {
                "last_scraped_at": row["last_scraped_at"] if row else None,
                "last_tagged_at": row["last_tagged_at"] if row else None,
            }

    async def get_event(self, event_id: str) -> Event | None:
        async with self.session() as session:
            result = await session.execute(text("SELECT * FROM events WHERE id = :id"), {"id": _uuid_param(event_id)})
            row = result.mappings().first()
            return _row_to_event(row) if row else None

    async def get_recent_events(self, days: int = 14) -> list[Event]:
        now, future = time_window(days)
        async with self.session() as session:
            result = await session.execute(
                text("SELECT * FROM events WHERE start_time >= :now AND start_time <= :future ORDER BY start_time"),
                {"now": now, "future": future},
            )
            return [_row_to_event(row) for row in result.mappings().all()]

    async def get_events_between(
        self,
        start: datetime,
        end: datetime,
        *,
        attended: str = "",
    ) -> list[Event]:
        conditions = ["start_time >= :start", "start_time < :end"]
        params: dict[str, Any] = {"start": start, "end": end}
        if attended == "yes":
            conditions.append("attended = true")
        elif attended == "no":
            conditions.append("attended = false")
        sql = f"SELECT * FROM events WHERE {' AND '.join(conditions)} ORDER BY start_time"
        async with self.session() as session:
            result = await session.execute(text(sql), params)
            return [_row_to_event(row) for row in result.mappings().all()]

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
        now, future = time_window(days)
        conditions = ["start_time >= :now", "start_time <= :future"]
        params: dict[str, Any] = {"now": now, "future": future}
        if q:
            conditions.append("(title ILIKE :q OR description ILIKE :q)")
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
            conditions.append("attended = true")
        elif attended == "no":
            conditions.append("attended = false")
        if score_min is not None:
            conditions.append("tags IS NOT NULL AND CAST(tags->>'toddler_score' AS INTEGER) >= :score_min")
            params["score_min"] = score_min
        where = " AND ".join(conditions)
        valid_sorts = {
            "start_time": "start_time",
            "-start_time": "start_time DESC",
            "title": "title",
            "-title": "title DESC",
            "city": "location_city",
            "-city": "location_city DESC",
            "source": "source",
            "-source": "source DESC",
            "score": "CAST(tags->>'toddler_score' AS INTEGER)",
            "-score": "CAST(tags->>'toddler_score' AS INTEGER) DESC",
        }
        order_clause = valid_sorts.get(sort, "start_time")
        offset = (page - 1) * per_page
        params |= {"limit": per_page, "offset": offset}
        async with self.session() as session:
            count_result = await session.execute(text(f"SELECT COUNT(*) FROM events WHERE {where}"), params)
            total_row = count_result.first()
            total = int(total_row[0] if total_row else 0)
            result = await session.execute(
                text(
                    f"SELECT * FROM events WHERE {where} ORDER BY {order_clause} LIMIT :limit OFFSET :offset"
                ),
                params,
            )
            return [_row_to_event(row) for row in result.mappings().all()], total

    async def get_filter_options(self) -> dict[str, list[str]]:
        async with self.session() as session:
            city_rows = (
                await session.execute(text("SELECT DISTINCT location_city FROM events ORDER BY location_city"))
            ).all()
            source_rows = (await session.execute(text("SELECT DISTINCT source FROM events ORDER BY source"))).all()
            return {
                "cities": [str(row[0]) for row in city_rows],
                "sources": [str(row[0]) for row in source_rows],
            }

    async def create_source(self, source: Source) -> str:
        async with self.session() as session:
            await session.execute(
                text(
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
                    """
                ),
                {
                    **source.model_dump(),
                    "id": _uuid_param(source.id),
                    "user_id": _uuid_param(source.user_id),
                },
            )
            await session.commit()
            return source.id

    async def get_source(self, source_id: str) -> Source | None:
        async with self.session() as session:
            result = await session.execute(text("SELECT * FROM sources WHERE id = :id"), {"id": _uuid_param(source_id)})
            row = result.mappings().first()
            return _row_to_source(row) if row else None

    async def get_source_by_url(self, url: str) -> Source | None:
        async with self.session() as session:
            result = await session.execute(text("SELECT * FROM sources WHERE url = :url"), {"url": url})
            row = result.mappings().first()
            return _row_to_source(row) if row else None

    async def get_all_sources(self) -> list[Source]:
        async with self.session() as session:
            result = await session.execute(text("SELECT * FROM sources ORDER BY created_at DESC"))
            return [_row_to_source(row) for row in result.mappings().all()]

    async def get_enabled_sources(self) -> list[Source]:
        async with self.session() as session:
            result = await session.execute(
                text(
                    "SELECT * FROM sources WHERE enabled = true AND builtin = false AND status IN ('active', 'stale') ORDER BY created_at"
                )
            )
            return [_row_to_source(row) for row in result.mappings().all()]

    async def update_source_recipe(self, source_id: str, recipe_json: str, status: str = "active") -> None:
        async with self.session() as session:
            await session.execute(
                text("UPDATE sources SET recipe_json = :recipe_json, status = :status, updated_at = :now WHERE id = :id"),
                {"recipe_json": recipe_json, "status": status, "now": datetime.now(tz=UTC), "id": _uuid_param(source_id)},
            )
            await session.commit()

    async def update_source_status(
        self,
        source_id: str,
        *,
        status: str | None = None,
        count: int | None = None,
        error: str | None = None,
    ) -> None:
        sets = ["updated_at = :now"]
        params: dict[str, Any] = {"now": datetime.now(tz=UTC), "id": _uuid_param(source_id)}
        if status is not None:
            sets.append("status = :status")
            params["status"] = status
        if count is not None:
            sets += ["last_event_count = :count", "last_scraped_at = :now", "last_error = NULL"]
            params["count"] = count
            if count == 0:
                sets.append("status = 'stale'")
            elif status is None:
                sets.append("status = 'active'")
        if error is not None:
            sets.append("last_error = :error")
            params["error"] = error
        async with self.session() as session:
            await session.execute(text(f"UPDATE sources SET {', '.join(sets)} WHERE id = :id"), params)
            await session.commit()

    async def toggle_source(self, source_id: str) -> bool:
        async with self.session() as session:
            await session.execute(
                text(
                    """
                    UPDATE sources
                    SET enabled = NOT enabled,
                        status = CASE WHEN enabled THEN 'disabled' ELSE 'active' END,
                        updated_at = :now
                    WHERE id = :id
                    """
                ),
                {"now": datetime.now(tz=UTC), "id": _uuid_param(source_id)},
            )
            await session.commit()
        source = await self.get_source(source_id)
        return source.enabled if source else False

    async def delete_source(self, source_id: str) -> None:
        source = await self.get_source(source_id)
        async with self.session() as session:
            if source and not source.builtin:
                await session.execute(
                    text("DELETE FROM events WHERE source = :source"),
                    {"source": f"custom:{source_id}"},
                )
            await session.execute(text("DELETE FROM sources WHERE id = :id"), {"id": _uuid_param(source_id)})
            await session.commit()

    async def mark_attended(self, event_id: str) -> None:
        await self.set_attended(event_id, attended=True)

    async def set_attended(self, event_id: str, *, attended: bool) -> None:
        async with self.session() as session:
            await session.execute(
                text("UPDATE events SET attended = :attended WHERE id = :id"),
                {"attended": attended, "id": _uuid_param(event_id)},
            )
            await session.commit()

    async def set_attended_bulk(self, event_ids: list[str], *, attended: bool) -> None:
        if not event_ids:
            return
        stmt = text("UPDATE events SET attended = :attended WHERE id = ANY(:event_ids)").bindparams(
            bindparam("event_ids")
        )
        async with self.session() as session:
            await session.execute(
                stmt,
                {"attended": attended, "event_ids": [_uuid_param(event_id) for event_id in event_ids]},
            )
            await session.commit()

    async def create_job(self, job: Job) -> str:
        async with self.session() as session:
            await session.execute(
                text(
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
                    """
                ),
                {
                    **job.model_dump(),
                    "id": _uuid_param(job.id),
                    "owner_user_id": _uuid_param(job.owner_user_id),
                    "source_id": _uuid_param(job.source_id),
                },
            )
            await session.commit()
            return job.id

    async def update_job(self, job_id: str, **fields: Any) -> None:
        allowed = {"state", "detail", "result_json", "error", "started_at", "finished_at"}
        sets: list[str] = []
        params: dict[str, Any] = {"id": _uuid_param(job_id)}
        for key, value in fields.items():
            if key not in allowed:
                continue
            sets.append(f"{key} = :{key}")
            params[key] = value
        if not sets:
            return
        async with self.session() as session:
            await session.execute(text(f"UPDATE jobs SET {', '.join(sets)} WHERE id = :id"), params)
            await session.commit()

    async def get_job(self, job_id: str) -> Job | None:
        async with self.session() as session:
            result = await session.execute(text("SELECT * FROM jobs WHERE id = :id"), {"id": _uuid_param(job_id)})
            row = result.mappings().first()
            return _row_to_job(row) if row else None

    async def get_active_job_by_key(self, job_key: str) -> Job | None:
        async with self.session() as session:
            result = await session.execute(
                text(
                    "SELECT * FROM jobs WHERE job_key = :job_key AND state = 'running' ORDER BY created_at DESC LIMIT 1"
                ),
                {"job_key": job_key},
            )
            row = result.mappings().first()
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
        q = normalize_search_query(q)
        if q:
            sql += " AND (label ILIKE :q OR detail ILIKE :q OR error ILIKE :q)"
            params["q"] = f"%{q}%"
        sql += " ORDER BY created_at DESC LIMIT :limit"
        async with self.session() as session:
            result = await session.execute(text(sql), params)
            return [_row_to_job(row) for row in result.mappings().all()]

    async def list_job_kinds(self, *, owner_user_id: str) -> list[str]:
        async with self.session() as session:
            result = await session.execute(
                text("SELECT DISTINCT kind FROM jobs WHERE owner_user_id = :owner_user_id ORDER BY kind ASC"),
                {"owner_user_id": owner_user_id},
            )
            return [str(row[0]) for row in result.all() if row[0]]

    async def fail_stale_jobs(self, *, max_age_seconds: int) -> int:
        now = datetime.now(tz=UTC)
        cutoff = now - timedelta(seconds=max_age_seconds)
        async with self.session() as session:
            result = await session.execute(
                text(
                    """
                    UPDATE jobs
                    SET state = 'failed',
                        detail = 'Failed',
                        error = CASE WHEN error != '' THEN error ELSE 'Job exceeded max runtime or worker stopped unexpectedly' END,
                        finished_at = :now
                    WHERE state = 'running' AND COALESCE(started_at, created_at) < :cutoff
                    """
                ),
                {"now": now, "cutoff": cutoff},
            )
            await session.commit()
            return result.rowcount or 0

    async def create_user(self, user: User) -> str:
        async with self.session() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO users (
                        id, email, display_name, password_hash,
                        home_city, preferred_cities, theme,
                        notification_channels, email_to, sms_to, child_name, onboarding_complete,
                        interest_profile, created_at, updated_at
                    ) VALUES (
                        :id, :email, :display_name, :password_hash,
                        :home_city, CAST(:preferred_cities AS jsonb), :theme,
                        CAST(:notification_channels AS jsonb), :email_to, :sms_to, :child_name, :onboarding_complete,
                        CAST(:interest_profile AS jsonb), :created_at, :updated_at
                    )
                    """
                ),
                {
                    "id": _uuid_param(user.id),
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
                    "onboarding_complete": user.onboarding_complete,
                    "interest_profile": json.dumps(user.interest_profile.model_dump()),
                    "created_at": user.created_at,
                    "updated_at": user.updated_at,
                },
            )
            await session.commit()
            return user.id

    async def get_user(self, user_id: str) -> User | None:
        async with self.session() as session:
            result = await session.execute(text("SELECT * FROM users WHERE id = :id"), {"id": _uuid_param(user_id)})
            row = result.mappings().first()
            return _row_to_user(row) if row else None

    async def get_user_by_email(self, email: str) -> User | None:
        async with self.session() as session:
            result = await session.execute(
                text("SELECT * FROM users WHERE email = :email"),
                {"email": normalize_email(email)},
            )
            row = result.mappings().first()
            return _row_to_user(row) if row else None

    async def update_user(self, user_id: str, **fields: Any) -> None:
        allowed = USER_UPDATE_FIELDS
        params: dict[str, Any] = {"id": _uuid_param(user_id), "updated_at": datetime.now(tz=UTC)}
        sets = ["updated_at = :updated_at"]
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key in {"preferred_cities", "notification_channels"}:
                sets.append(f"{key} = CAST(:{key} AS jsonb)")
                params[key] = json.dumps(value)
            elif key == "interest_profile":
                sets.append(f"{key} = CAST(:{key} AS jsonb)")
                params[key] = json.dumps(value.model_dump() if hasattr(value, "model_dump") else value)
            else:
                sets.append(f"{key} = :{key}")
                params[key] = value
        async with self.session() as session:
            await session.execute(text(f"UPDATE users SET {', '.join(sets)} WHERE id = :id"), params)
            await session.commit()

    async def get_all_users(self) -> list[User]:
        async with self.session() as session:
            result = await session.execute(text("SELECT * FROM users ORDER BY created_at"))
            return [_row_to_user(row) for row in result.mappings().all()]

    async def get_user_sources(self, user_id: str) -> list[Source]:
        async with self.session() as session:
            result = await session.execute(
                text(
                    "SELECT * FROM sources WHERE user_id = :uid ORDER BY builtin DESC, city ASC, name ASC, created_at DESC"
                ),
                {"uid": _uuid_param(user_id)},
            )
            return [_row_to_source(row) for row in result.mappings().all()]

    async def get_user_source_by_url(self, user_id: str, url: str) -> Source | None:
        async with self.session() as session:
            result = await session.execute(
                text("SELECT * FROM sources WHERE user_id = :user_id AND url = :url"),
                {"user_id": _uuid_param(user_id), "url": url},
            )
            row = result.mappings().first()
            return _row_to_source(row) if row else None

    async def dedupe_existing_events(self) -> dict[str, int]:
        async with self.session() as session:
            result = await session.execute(text("SELECT * FROM events ORDER BY start_time, scraped_at"))
            events = [_row_to_event(row) for row in result.mappings().all()]
            total = len(events)
            merged = 0
            buckets: dict[str, list[Event]] = {}
            for event in events:
                bucket_key = (
                    f"{event.location_city.lower().strip()}|{event.start_time.date().isoformat()}|{event.start_time.hour // 2}"
                )
                buckets.setdefault(bucket_key, []).append(event)
            for bucket_events in buckets.values():
                if len(bucket_events) < 2:
                    continue
                canonical: list[Event] = []
                for event in bucket_events:
                    duplicate_of: Event | None = None
                    for c in canonical:
                        if event_fingerprint(event) == event_fingerprint(c) or title_similarity(event.title, c.title) >= 0.75:
                            duplicate_of = c
                            break
                    if not duplicate_of:
                        canonical.append(event)
                        continue
                    await session.execute(
                        text(
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
                                scraped_at = CASE WHEN scraped_at < :scraped_at THEN :scraped_at ELSE scraped_at END,
                                tags = COALESCE(tags, CAST(:tags AS jsonb)),
                                score_breakdown = COALESCE(score_breakdown, CAST(:score_breakdown AS jsonb)),
                                attended = attended OR :attended
                            WHERE id = :canonical_id
                            """
                        ),
                        self._event_params(event) | {"canonical_id": duplicate_of.id},
                    )
                    await session.execute(text("DELETE FROM events WHERE id = :id"), {"id": event.id})
                    merged += 1
            await session.commit()
            return {"total_scanned": total, "merged": merged, "remaining": total - merged}

    async def __aenter__(self) -> PostgresDatabase:
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)
