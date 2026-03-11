"""Helpers for rendering and starting persisted web background jobs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse

from src.db.models import Job, User
from src.web.common import ensure_csrf_token, get_templates, toast
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


def job_result_summary(job: Job) -> str | None:
    """Return a concise success summary for structured job results."""
    result = job_result_value(job)
    if job.state == "running" and isinstance(result, dict):
        summary = result.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary

    if isinstance(result, int):
        noun = {
            "scrape": "events scraped",
            "tag": "events tagged",
            "dedupe": "events merged",
            "source-test": "events found",
        }.get(job.kind, "items processed")
        return f"{result} {noun}"
    if isinstance(result, str) and result.strip():
        return result
    if isinstance(result, dict):
        if job.kind == "pipeline":
            scraped = result.get("scraped")
            tagged = result.get("tagged")
            failed = result.get("failed")
            if all(isinstance(value, int) for value in (scraped, tagged, failed)):
                return f"{scraped} events scraped · {tagged} tagged · {failed} failed"
        if job.kind == "notify":
            summary = result.get("summary")
            if isinstance(summary, str) and summary.strip():
                return summary
        if job.kind == "source-test":
            count = result.get("count")
            if isinstance(count, int):
                return f"{count} events found"
        if job.kind == "source-analyze":
            strategy = result.get("strategy")
            confidence = result.get("confidence")
            if isinstance(confidence, (int, float)):
                if strategy:
                    return f"{strategy} strategy at {confidence:.0%} confidence"
                return f"{confidence:.0%} confidence"
        summary = result.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary
    return None


def job_status_message(job: Job) -> str:
    """Return a human-readable job status string."""
    if job.state == "running":
        summary = job_result_summary(job)
        return f"{job.label} is running… {summary}" if summary else f"{job.label} is running…"
    if job.state == "cancelled":
        return f"{job.label} was cancelled"
    if job.state == "failed":
        return f"{job.label} failed: {job.error or 'Unknown error'}"
    summary = job_result_summary(job)
    return f"{job.label} completed: {summary}" if summary else f"{job.label} completed"


def job_template_context(
    job: Job,
    *,
    target_id: str,
    refresh_path: str = "",
    refresh_select: str = "",
    refresh_target_id: str = "",
    auto_refresh_history: bool = False,
) -> dict[str, Any]:
    """Build template context for a rendered job card."""
    result = job_result_value(job)
    progress = result if isinstance(result, dict) else None
    return {
        "job": job,
        "target_id": target_id,
        "message": job_status_message(job),
        "started_at": fmt_job_time(job.started_at or job.created_at),
        "finished_at": fmt_job_time(job.finished_at),
        "result": result,
        "progress": progress,
        "result_summary": job_result_summary(job),
        "refresh_path": refresh_path,
        "refresh_select": refresh_select,
        "refresh_target_id": refresh_target_id,
        "auto_refresh_history": auto_refresh_history,
    }


def render_job_cards(
    jobs: list[Job],
    *,
    target_prefix: str,
    refresh_path: str = "",
    refresh_select: str = "",
    refresh_target_id: str = "",
    auto_refresh_history: bool = False,
) -> list[dict[str, Any]]:
    """Prepare template contexts for a collection of jobs."""
    return [
        job_template_context(
            job,
            target_id=f"{target_prefix}{job.id}",
            refresh_path=refresh_path,
            refresh_select=refresh_select,
            refresh_target_id=refresh_target_id,
            auto_refresh_history=auto_refresh_history,
        )
        for job in jobs
    ]


async def start_background_job(
    request: Request,
    *,
    user: User,
    database_url: str | None,
    kind: str,
    key: str,
    label: str,
    runner,
    target_id: str,
    source_id: str | None = None,
    extra_body: str = "",
) -> HTMLResponse:
    """Start or reuse a background job and return a polling job card."""
    job, created = await job_registry.start_unique(
        kind=kind,
        job_key=key,
        label=label,
        owner_user_id=user.id,
        source_id=source_id,
        runner=runner,
        database_url=database_url,
    )
    if created:
        message = f"{label} started in the background"
        variant = "info"
    else:
        message = f"{label} is already running"
        variant = "warning"

    ensure_csrf_token(request)
    body = (
        get_templates(request)
        .get_template("partials/_job_status.html")
        .render(
            request=request,
            csrf_token=request.session.get("csrf_token", ""),
            **job_template_context(job, target_id=target_id),
        )
    )
    return toast(message, variant, body=extra_body + body)
