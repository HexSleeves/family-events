"""Runtime task tracker for persisted web-triggered background jobs."""

from __future__ import annotations

import asyncio
import json
import logging
import time
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


def _duration_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def _runtime_log(level: int, event: str, **context: object) -> None:
    logger.log(
        level,
        event,
        extra={key: value for key, value in context.items() if value is not None},
    )


def _error_details(exc: BaseException) -> tuple[str, str]:
    message = str(exc).strip() or repr(exc)
    return type(exc).__name__, message


def _open_database(*, database_url: str | None = None):
    if database_url is None:
        return Database()
    try:
        return Database(database_url=database_url)
    except TypeError:
        return Database()


@dataclass(slots=True)
class BackgroundJobContext:
    """Helper passed to background runners for progress updates."""

    job_id: str
    database_url: str | None = None

    async def update(self, *, detail: str | None = None, result: Any | None = None) -> None:
        fields: dict[str, Any] = {}
        if detail is not None:
            fields["detail"] = detail
        if result is not None:
            fields["result_json"] = json.dumps(result)
        if not fields:
            return
        async with _open_database(database_url=self.database_url) as db:
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

    async def recover_stale_jobs(self, *, database_url: str | None = None) -> int:
        """Fail stale persisted jobs so they no longer block duplicate prevention."""
        async with _open_database(database_url=database_url) as db:
            updated = await db.fail_stale_jobs(
                max_age_seconds=settings.background_job_timeout_seconds
            )
        if updated:
            _runtime_log(
                logging.WARNING,
                "background_job_recovered_stale_jobs",
                recovered_count=updated,
                timeout_seconds=settings.background_job_timeout_seconds,
            )
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
        database_url: str | None = None,
    ) -> tuple[Job, bool]:
        async with self._lock:
            active_id = self._active_ids_by_key.get(job_key)
            if active_id and active_id in self._active_by_id:
                async with _open_database(database_url=database_url) as db:
                    existing = await db.get_job(active_id)
                if existing and existing.state == "running":
                    existing.detail = existing.detail or "Running…"
                    _runtime_log(
                        logging.INFO,
                        "background_job_already_running",
                        job_id=existing.id,
                        job_key=job_key,
                        kind=existing.kind,
                        source_id=existing.source_id,
                    )
                    return existing, False
                self._active_ids_by_key.pop(job_key, None)
                self._active_by_id.pop(active_id, None)

            await self.recover_stale_jobs(database_url=database_url)
            async with _open_database(database_url=database_url) as db:
                persisted = await db.get_active_job_by_key(job_key)
                if persisted:
                    _runtime_log(
                        logging.INFO,
                        "background_job_already_running",
                        job_id=persisted.id,
                        job_key=job_key,
                        kind=persisted.kind,
                        source_id=persisted.source_id,
                    )
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

            task = asyncio.create_task(
                self._run(
                    job_id=job.id,
                    job_key=job_key,
                    kind=kind,
                    label=label,
                    source_id=source_id,
                    runner=runner,
                    database_url=database_url,
                )
            )
            self._active_by_id[job.id] = ActiveJob(id=job.id, job_key=job_key, task=task)
            self._active_ids_by_key[job_key] = job.id
            self._trim_locked()
            return job, True

    async def _run(
        self,
        *,
        job_id: str,
        job_key: str,
        kind: str,
        label: str,
        source_id: str | None,
        runner: Callable[[BackgroundJobContext], Awaitable[Any]],
        database_url: str | None,
    ) -> None:
        started_at = datetime.now(tz=UTC)
        runtime_started = time.perf_counter()
        context = BackgroundJobContext(job_id=job_id, database_url=database_url)
        async with _open_database(database_url=database_url) as db:
            await db.update_job(job_id, detail="Running…", started_at=started_at)
        _runtime_log(
            logging.INFO,
            "background_job_started",
            stage="run",
            job_id=job_id,
            job_key=job_key,
            kind=kind,
            label=label,
            source_id=source_id,
        )
        try:
            result = await asyncio.wait_for(
                runner(context),
                timeout=settings.background_job_timeout_seconds,
            )
            async with _open_database(database_url=database_url) as db:
                await db.update_job(
                    job_id,
                    state="succeeded",
                    detail="Completed",
                    result_json=json.dumps(result),
                    finished_at=datetime.now(tz=UTC),
                    error="",
                )
            _runtime_log(
                logging.INFO,
                "background_job_succeeded",
                stage="run",
                job_id=job_id,
                job_key=job_key,
                kind=kind,
                label=label,
                source_id=source_id,
                duration_ms=_duration_ms(runtime_started),
                result_type=type(result).__name__,
            )
        except asyncio.CancelledError:
            _runtime_log(
                logging.INFO,
                "background_job_cancelled",
                stage="run",
                job_id=job_id,
                job_key=job_key,
                kind=kind,
                label=label,
                source_id=source_id,
                duration_ms=_duration_ms(runtime_started),
            )
            raise
        except TimeoutError:
            async with _open_database(database_url=database_url) as db:
                await db.update_job(
                    job_id,
                    state="failed",
                    detail="Timed out",
                    error=(
                        f"Job exceeded max runtime ({settings.background_job_timeout_seconds}s)"
                    ),
                    finished_at=datetime.now(tz=UTC),
                )
            _runtime_log(
                logging.ERROR,
                "background_job_failed",
                stage="run",
                job_id=job_id,
                job_key=job_key,
                kind=kind,
                label=label,
                source_id=source_id,
                error_type="TimeoutError",
                error_message=(
                    f"Job exceeded max runtime ({settings.background_job_timeout_seconds}s)"
                ),
                duration_ms=_duration_ms(runtime_started),
            )
        except Exception as exc:
            error_type, error_message = _error_details(exc)
            async with _open_database(database_url=database_url) as db:
                await db.update_job(
                    job_id,
                    state="failed",
                    detail="Failed",
                    error=error_message,
                    finished_at=datetime.now(tz=UTC),
                )
            _runtime_log(
                logging.ERROR,
                "background_job_failed",
                stage="run",
                job_id=job_id,
                job_key=job_key,
                kind=kind,
                label=label,
                source_id=source_id,
                error_type=error_type,
                error_message=error_message,
                duration_ms=_duration_ms(runtime_started),
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

    async def cancel(
        self, *, job_id: str, owner_user_id: str, database_url: str | None = None
    ) -> Job | None:
        """Cancel an active job owned by the given user, if possible."""
        async with self._lock:
            async with _open_database(database_url=database_url) as db:
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
            if updated:
                _runtime_log(
                    logging.INFO,
                    "background_job_cancel_requested",
                    job_id=updated.id,
                    job_key=updated.job_key,
                    kind=updated.kind,
                    source_id=updated.source_id,
                )
            return updated


job_registry = JobRegistry()
