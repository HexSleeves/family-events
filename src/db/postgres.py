"""Postgres-backed database implementation scaffold."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.db.models import Event, EventTags, InterestProfile, Job, User
from src.db.session import get_engine, get_sessionmaker


def _row_to_event(row: Any) -> Event:
    data = dict(row)
    tags = data.get("tags")
    data["tags"] = EventTags.model_validate(tags) if tags else None
    return Event.model_validate(data)


def _row_to_user(row: Any) -> User:
    data = dict(row)
    raw_profile = data.get("interest_profile") or {}
    data["interest_profile"] = (
        InterestProfile.model_validate(raw_profile) if raw_profile else InterestProfile()
    )
    return User.model_validate(data)


def _row_to_job(row: Any) -> Job:
    return Job.model_validate(dict(row))


class PostgresDatabase:
    """Incremental Postgres implementation behind the existing DB API."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.engine: AsyncEngine | None = None
        self.sessionmaker: async_sessionmaker[AsyncSession] | None = None

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

    async def get_event(self, event_id: str) -> Event | None:
        async with self.session() as session:
            result = await session.execute(text("SELECT * FROM events WHERE id = :id"), {"id": event_id})
            row = result.mappings().first()
            return _row_to_event(row) if row else None

    async def get_recent_events(self, days: int = 14) -> list[Event]:
        now = datetime.now(tz=UTC)
        future = now + timedelta(days=days)
        async with self.session() as session:
            result = await session.execute(
                text(
                    """
                    SELECT * FROM events
                    WHERE start_time >= :now AND start_time <= :future
                    ORDER BY start_time
                    """
                ),
                {"now": now, "future": future},
            )
            return [_row_to_event(row) for row in result.mappings().all()]

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
                    "created_at": job.created_at,
                    "started_at": job.started_at,
                    "finished_at": job.finished_at,
                },
            )
            await session.commit()
            return job.id

    async def get_job(self, job_id: str) -> Job | None:
        async with self.session() as session:
            result = await session.execute(text("SELECT * FROM jobs WHERE id = :id"), {"id": job_id})
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
        q = q.strip()
        if q:
            sql += " AND (label ILIKE :q OR detail ILIKE :q OR error ILIKE :q)"
            params["q"] = f"%{q}%"
        sql += " ORDER BY created_at DESC LIMIT :limit"
        async with self.session() as session:
            result = await session.execute(text(sql), params)
            return [_row_to_job(row) for row in result.mappings().all()]

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
                        error = CASE
                            WHEN error != '' THEN error
                            ELSE 'Job exceeded max runtime or worker stopped unexpectedly'
                        END,
                        finished_at = :now
                    WHERE state = 'running'
                      AND COALESCE(started_at, created_at) < :cutoff
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
            result = await session.execute(text("SELECT * FROM users WHERE id = :id"), {"id": user_id})
            row = result.mappings().first()
            return _row_to_user(row) if row else None

    async def get_user_by_email(self, email: str) -> User | None:
        async with self.session() as session:
            result = await session.execute(
                text("SELECT * FROM users WHERE email = :email"),
                {"email": email.lower().strip()},
            )
            row = result.mappings().first()
            return _row_to_user(row) if row else None

    async def get_all_users(self) -> list[User]:
        async with self.session() as session:
            result = await session.execute(text("SELECT * FROM users ORDER BY created_at"))
            return [_row_to_user(row) for row in result.mappings().all()]

    async def __aenter__(self) -> PostgresDatabase:
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"Postgres backend method not implemented yet: {name}")
