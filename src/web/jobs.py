"""Runtime task tracker for persisted web-triggered background jobs."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.config import settings
from src.db.database import create_database
from src.db.models import Job

logger = logging.getLogger("uvicorn.error")

Database = create_database


@dataclass(slots=True)
class BackgroundJobContext:
    """Helper passed to background runners for progress updates."""

    job_id: str

    async def update(self, *, detail: str | None = None, result: Any | None = None) -> None:
        fields: dict[str, Any] = {}
        if detail is not None:
            fields["detail"] = detail
        if result is not None:
            fields["result_json"] = json.dumps(result)
        if not fields:
            return
        async with Database() as db:
            await db.update_job(self.job_id, **fields)


@dataclass(slots=True)
class ActiveJob:
    id: str
    job_key: str
    task: asyncio.Task[Any]
    started_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))


class JobRegistry:
    """Track active tasks while persisting status/history in the configured database."""

    def __init__(self) -> None:
        self._active_by_id: OrderedDict[str, ActiveJob] = OrderedDict()
        self._active_ids_by_key: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._max_active = 200

    async def recover_stale_jobs(self) -> int:
        """Fail stale persisted jobs so they no longer block duplicate prevention."""
        async with Database() as db:
            updated = await db.fail_stale_jobs(
                max_age_seconds=settings.background_job_timeout_seconds
            )
        if updated:
            logger.warning("background_job_recovered_stale_jobs count=%s", updated)
        return updated

    async def start_unique(
        self,
        *,
        kind: str,
        job_key: str,
        label: str,
        owner_user_id: str,
        source_id: str | None,
        runner: Callable[[BackgroundJobContext], Awaitable[Any]],
    ) -> tuple[Job, bool]:
        async with self._lock:
            active_id = self._active_ids_by_key.get(job_key)
            if active_id and active_id in self._active_by_id:
                async with Database() as db:
                    existing = await db.get_job(active_id)
                if existing and existing.state == "running":
                    existing.detail = existing.detail or "Running…"
                    return existing, False
                self._active_ids_by_key.pop(job_key, None)
                self._active_by_id.pop(active_id, None)

            await self.recover_stale_jobs()
            async with Database() as db:
                persisted = await db.get_active_job_by_key(job_key)
                if persisted:
                    return persisted, False

                job = Job(
                    kind=kind,
                    job_key=job_key,
                    label=label,
                    owner_user_id=owner_user_id,
                    source_id=source_id,
                    state="running",
                    detail="Starting…",
                )
                await db.create_job(job)

            task = asyncio.create_task(self._run(job.id, job_key, runner))
            self._active_by_id[job.id] = ActiveJob(id=job.id, job_key=job_key, task=task)
            self._active_ids_by_key[job_key] = job.id
            self._trim_locked()
            return job, True

    async def _run(
        self,
        job_id: str,
        job_key: str,
        runner: Callable[[BackgroundJobContext], Awaitable[Any]],
    ) -> None:
        started_at = datetime.now(tz=UTC)
        context = BackgroundJobContext(job_id=job_id)
        async with Database() as db:
            await db.update_job(job_id, detail="Running…", started_at=started_at)
        try:
            result = await asyncio.wait_for(
                runner(context),
                timeout=settings.background_job_timeout_seconds,
            )
            async with Database() as db:
                await db.update_job(
                    job_id,
                    state="succeeded",
                    detail="Completed",
                    result_json=json.dumps(result),
                    finished_at=datetime.now(tz=UTC),
                    error="",
                )
        except TimeoutError:
            async with Database() as db:
                await db.update_job(
                    job_id,
                    state="failed",
                    detail="Timed out",
                    error=(
                        "Job exceeded max runtime "
                        f"({settings.background_job_timeout_seconds}s)"
                    ),
                    finished_at=datetime.now(tz=UTC),
                )
        except Exception as exc:
            async with Database() as db:
                await db.update_job(
                    job_id,
                    state="failed",
                    detail="Failed",
                    error=str(exc),
                    finished_at=datetime.now(tz=UTC),
                )
        finally:
            async with self._lock:
                self._active_by_id.pop(job_id, None)
                if self._active_ids_by_key.get(job_key) == job_id:
                    self._active_ids_by_key.pop(job_key, None)

    def _trim_locked(self) -> None:
        while len(self._active_by_id) > self._max_active:
            oldest_id, oldest = self._active_by_id.popitem(last=False)
            if not oldest.task.done():
                self._active_by_id[oldest_id] = oldest
                break
            if self._active_ids_by_key.get(oldest.job_key) == oldest_id:
                self._active_ids_by_key.pop(oldest.job_key, None)

    async def cancel(self, *, job_id: str, owner_user_id: str) -> Job | None:
        """Cancel an active job owned by the given user, if possible."""
        async with self._lock:
            async with Database() as db:
                job = await db.get_job(job_id)
                if not job or job.owner_user_id != owner_user_id:
                    return None
                if job.state != "running":
                    return job

                active = self._active_by_id.get(job_id)
                if active and not active.task.done():
                    active.task.cancel()

                await db.update_job(
                    job_id,
                    state="cancelled",
                    detail="Cancelled",
                    error="Cancelled by user",
                    finished_at=datetime.now(tz=UTC),
                )
                updated = await db.get_job(job_id)

            self._active_by_id.pop(job_id, None)
            if updated and self._active_ids_by_key.get(updated.job_key) == job_id:
                self._active_ids_by_key.pop(updated.job_key, None)
            return updated


job_registry = JobRegistry()
