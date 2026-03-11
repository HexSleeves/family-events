"""Job history and job action routes."""

from __future__ import annotations

import asyncio
from urllib.parse import quote_plus

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.scheduler import SYSTEM_USER_EMAIL
from src.web.auth import ensure_csrf_token, get_current_user
from src.web.common import (
    check_rate_limit,
    ctx,
    get_current_user_or_redirect,
    get_db,
    get_templates,
    require_login_and_csrf,
    sse_stream,
    template_response,
    toast,
)
from src.web.jobs import job_registry
from src.web.jobs_ui import job_template_context, render_job_cards

router = APIRouter()


async def _system_user_id(request: Request) -> str | None:
    system_user = await get_db(request).get_user_by_email(SYSTEM_USER_EMAIL)
    return system_user.id if system_user else None


@router.get("/api/jobs/{job_id}", response_class=HTMLResponse)
async def api_job_status(
    request: Request,
    job_id: str,
    target_id: str = "job-status",
    allow_shared: str = "",
):
    db = get_db(request)
    user = await get_current_user(request, db)
    if not user:
        return HTMLResponse("", status_code=401)

    job = await db.get_job(job_id)
    shared_allowed = allow_shared == "1" and job is not None
    shared_owner_id = await _system_user_id(request) if shared_allowed else None
    can_view_shared = shared_allowed and shared_owner_id is not None and job is not None
    can_view_shared = bool(can_view_shared and job.owner_user_id == shared_owner_id)
    if not job or (job.owner_user_id != user.id and not can_view_shared):
        return HTMLResponse("", status_code=404)

    return template_response(
        request,
        "partials/_job_status.html",
        {
            "request": request,
            "csrf_token": ensure_csrf_token(request),
            **job_template_context(
                job,
                target_id=target_id,
                can_cancel=job.owner_user_id == user.id,
                allow_shared_view=can_view_shared,
            ),
        },
    )


@router.post("/api/jobs/{job_id}/cancel", response_class=HTMLResponse)
async def api_cancel_job(request: Request, job_id: str, target_id: str = "job-status"):
    db = get_db(request)
    user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := check_rate_limit(request, "api_cancel_job"):
        return throttled

    job = await job_registry.cancel(
        job_id=job_id, owner_user_id=user.id, database_url=db.database_url
    )
    if not job:
        return toast("Job not found", "error", status_code=404)

    body = (
        get_templates(request)
        .get_template("partials/_job_status.html")
        .render(
            request=request,
            csrf_token=ensure_csrf_token(request),
            **job_template_context(job, target_id=target_id),
        )
    )
    if job.state == "running":
        return toast("Job is still running", "warning", body=body)
    return toast("Job cancelled", "success", body=body)


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_page(
    request: Request,
    state: str = "",
    kind: str = "",
    source_id: str = "",
    q: str = "",
    scope: str = "mine",
):
    db = get_db(request)
    user, redirect = await get_current_user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None

    selected_scope = scope if scope in {"mine", "shared"} else "mine"
    selected_source_id = source_id.strip() or None
    selected_state = state.strip() or None
    selected_kind = kind.strip() or None
    search_query = q.strip()
    shared_owner_id = await _system_user_id(request)
    owner_user_id = user.id if selected_scope == "mine" else shared_owner_id

    jobs = await db.list_jobs(
        owner_user_id=owner_user_id,
        source_id=selected_source_id,
        state=selected_state,
        kind=selected_kind,
        q=search_query,
        limit=100,
    )
    job_cards = render_job_cards(
        jobs,
        target_prefix="jobs-page-",
        can_cancel=selected_scope == "mine",
        allow_shared_view=selected_scope == "shared",
        refresh_path=(
            f"/jobs?scope={quote_plus(selected_scope)}&state={quote_plus(state)}"
            f"&kind={quote_plus(kind)}&source_id={quote_plus(source_id)}&q={quote_plus(q)}"
        ),
        refresh_select="#jobs-list-panel",
        refresh_target_id="jobs-list-panel",
        auto_refresh_history=True,
    )
    sources = await db.get_user_sources(user.id) if selected_scope == "mine" else []
    job_kinds = await db.list_job_kinds(owner_user_id=owner_user_id)

    return template_response(
        request,
        "jobs.html",
        await ctx(
            request,
            active_page="jobs",
            jobs=jobs,
            job_cards=job_cards,
            sources=sources,
            job_kinds=job_kinds,
            selected_scope=selected_scope,
            selected_state=state,
            selected_kind=kind,
            selected_source_id=source_id,
            q=q,
        ),
    )


@router.get("/api/jobs/stream")
async def api_jobs_stream(request: Request, job_id: str = ""):
    """SSE endpoint that streams job status updates for a specific job."""
    db = get_db(request)
    user = await get_current_user(request, db)
    if not user:
        return HTMLResponse("", status_code=401)

    async def _generate():
        poll_interval = 1.0
        max_polls = 300
        polls = 0
        last_state = None

        while polls < max_polls:
            if await request.is_disconnected():
                break

            if job_id:
                job = await db.get_job(job_id)
                if not job:
                    yield ("error", "Job not found")
                    break

                tpl = get_templates(request)
                html = tpl.get_template("partials/_job_status.html").render(
                    request=request,
                    csrf_token=ensure_csrf_token(request),
                    **job_template_context(
                        job,
                        target_id=f"sse-job-{job.id}",
                        can_cancel=job.owner_user_id == user.id,
                    ),
                )
                yield ("job-update", html)

                if job.state != "running":
                    if last_state == "running":
                        yield ("job-complete", html)
                    break

                last_state = job.state

            polls += 1
            await asyncio.sleep(poll_interval)

    return await sse_stream(request, _generate())
