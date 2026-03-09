"""Job history and job action routes."""

from __future__ import annotations

from urllib.parse import quote_plus

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.web.auth import ensure_csrf_token, get_current_user
from src.web.common import (
    check_rate_limit,
    ctx,
    get_db,
    get_templates,
    htmx_redirect_or_redirect,
    require_login_and_csrf,
    template_response,
    toast,
)
from src.web.jobs import job_registry
from src.web.jobs_ui import job_template_context, render_job_cards

router = APIRouter()


@router.get("/api/jobs/{job_id}", response_class=HTMLResponse)
async def api_job_status(request: Request, job_id: str, target_id: str = "job-status"):
    db = get_db(request)
    user = await get_current_user(request, db)
    if not user:
        return HTMLResponse("", status_code=401)

    job = await db.get_job(job_id)
    if not job or job.owner_user_id != user.id:
        return HTMLResponse("", status_code=404)

    return get_templates(request).TemplateResponse(
        "partials/_job_status.html",
        {
            "request": request,
            "csrf_token": ensure_csrf_token(request),
            **job_template_context(job, target_id=target_id),
        },
    )


@router.post("/api/jobs/{job_id}/cancel", response_class=HTMLResponse)
async def api_cancel_job(request: Request, job_id: str, target_id: str = "job-status"):
    user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := check_rate_limit(request, "api_cancel_job"):
        return throttled

    job = await job_registry.cancel(job_id=job_id, owner_user_id=user.id)
    if not job:
        return toast("Job not found", "error", status_code=404)

    body = get_templates(request).get_template("partials/_job_status.html").render(
        request=request,
        csrf_token=ensure_csrf_token(request),
        **job_template_context(job, target_id=target_id),
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
):
    db = get_db(request)
    user = await get_current_user(request, db)
    if not user:
        return htmx_redirect_or_redirect(request, "/login")

    selected_source_id = source_id.strip() or None
    selected_state = state.strip() or None
    selected_kind = kind.strip() or None
    search_query = q.strip()

    jobs = await db.list_jobs(
        owner_user_id=user.id,
        source_id=selected_source_id,
        state=selected_state,
        kind=selected_kind,
        q=search_query,
        limit=100,
    )
    job_cards = render_job_cards(
        jobs,
        target_prefix="jobs-page-",
        refresh_path=f"/jobs?state={quote_plus(state)}&kind={quote_plus(kind)}&source_id={quote_plus(source_id)}&q={quote_plus(q)}",
        refresh_select="#jobs-list-panel",
        refresh_target_id="jobs-list-panel",
        auto_refresh_history=True,
    )
    sources = await db.get_user_sources(user.id)
    job_kinds = await db.list_job_kinds(owner_user_id=user.id)

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
            selected_state=state,
            selected_kind=kind,
            selected_source_id=source_id,
            q=q,
        ),
    )
