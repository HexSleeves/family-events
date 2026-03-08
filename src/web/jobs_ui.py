"""Helpers for rendering and starting persisted web background jobs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse

from src.db.models import Job, User
from src.web.common import get_templates, toast
from src.web.jobs import job_registry


def fmt_job_time(value: datetime | None) -> str:
    """Format job timestamps for the UI."""
    return value.astimezone(UTC).strftime("%b %d, %I:%M:%S %p UTC") if value else "—"


def job_result_value(job: Job) -> Any:
    """Parse persisted JSON job results when present."""
    if not job.result_json:
        return None
    try:
        return json.loads(job.result_json)
    except json.JSONDecodeError:
        return job.result_json


def job_status_message(job: Job) -> str:
    """Return a human-readable job status string."""
    if job.state == "running":
        return f"{job.label} is running…"
    if job.state == "failed":
        return f"{job.label} failed: {job.error or 'Unknown error'}"

    result = job_result_value(job)
    if isinstance(result, int):
        noun = {
            "scrape": "events scraped",
            "tag": "events tagged",
            "dedupe": "events merged",
            "source-test": "events found",
        }.get(job.kind, "items processed")
        return f"{job.label} completed: {result} {noun}"
    if isinstance(result, str) and result.strip():
        return f"{job.label} completed: {result}"
    return f"{job.label} completed"


def job_template_context(job: Job, *, target_id: str) -> dict[str, Any]:
    """Build template context for a rendered job card."""
    return {
        "job": job,
        "target_id": target_id,
        "message": job_status_message(job),
        "started_at": fmt_job_time(job.started_at or job.created_at),
        "finished_at": fmt_job_time(job.finished_at),
    }


async def start_background_job(
    request: Request,
    *,
    user: User,
    kind: str,
    key: str,
    label: str,
    runner,
    target_id: str,
    source_id: str | None = None,
) -> HTMLResponse:
    """Start or reuse a background job and return a polling job card."""
    job, created = await job_registry.start_unique(
        kind=kind,
        job_key=key,
        label=label,
        owner_user_id=user.id,
        source_id=source_id,
        runner=runner,
    )
    if created:
        message = f"{label} started in the background"
        variant = "info"
    else:
        message = f"{label} is already running"
        variant = "warning"

    body = get_templates(request).get_template("partials/_job_status.html").render(
        request=request,
        **job_template_context(job, target_id=target_id),
    )
    return toast(message, variant, body=body)
