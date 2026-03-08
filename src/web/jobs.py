"""In-memory background job registry for web-triggered tasks."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

JobState = Literal["running", "succeeded", "failed"]


@dataclass(slots=True)
class WebJob:
    id: str
    kind: str
    key: str
    label: str
    owner_user_id: str
    state: JobState = "running"
    detail: str = "Queued"
    result: Any = None
    error: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    task: asyncio.Task[Any] | None = None

    @property
    def is_done(self) -> bool:
        return self.state in {"succeeded", "failed"}


class JobRegistry:
    """Track lightweight background tasks started from the web UI."""

    def __init__(self) -> None:
        self._jobs_by_id: OrderedDict[str, WebJob] = OrderedDict()
        self._active_job_ids_by_key: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._max_jobs = 200

    async def start_unique(
        self,
        *,
        kind: str,
        key: str,
        label: str,
        owner_user_id: str,
        runner: Callable[[], Awaitable[Any]],
    ) -> tuple[WebJob, bool]:
        async with self._lock:
            active_id = self._active_job_ids_by_key.get(key)
            if active_id:
                existing = self._jobs_by_id.get(active_id)
                if existing and not existing.is_done:
                    return existing, False

            job = WebJob(
                id=str(uuid4()),
                kind=kind,
                key=key,
                label=label,
                owner_user_id=owner_user_id,
                detail="Starting…",
            )
            self._jobs_by_id[job.id] = job
            self._active_job_ids_by_key[key] = job.id
            self._trim_locked()
            job.task = asyncio.create_task(self._run(job, runner))
            return job, True

    async def _run(self, job: WebJob, runner: Callable[[], Awaitable[Any]]) -> None:
        job.started_at = datetime.now(tz=UTC)
        job.detail = "Running…"
        try:
            job.result = await runner()
            job.state = "succeeded"
            job.detail = "Completed"
        except Exception as exc:
            job.state = "failed"
            job.error = str(exc)
            job.detail = "Failed"
        finally:
            job.finished_at = datetime.now(tz=UTC)
            async with self._lock:
                if self._active_job_ids_by_key.get(job.key) == job.id:
                    self._active_job_ids_by_key.pop(job.key, None)

    async def get(self, job_id: str) -> WebJob | None:
        async with self._lock:
            return self._jobs_by_id.get(job_id)

    def _trim_locked(self) -> None:
        while len(self._jobs_by_id) > self._max_jobs:
            oldest_id, oldest = self._jobs_by_id.popitem(last=False)
            if self._active_job_ids_by_key.get(oldest.key) == oldest_id and not oldest.is_done:
                self._jobs_by_id[oldest_id] = oldest
                break
            if self._active_job_ids_by_key.get(oldest.key) == oldest_id:
                self._active_job_ids_by_key.pop(oldest.key, None)


job_registry = JobRegistry()
