"""FastAPI web admin for Family Events."""

from __future__ import annotations

import json
import logging
from collections import deque
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote_plus
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from src.config import settings
from src.db.database import Database, create_database
from src.db.models import InterestProfile
from src.notifications.formatter import format_console_message
from src.ranker.scoring import rank_events, score_event_breakdown
from src.ranker.weather import WeatherService, summarize_weekend_recommendation
from src.tagger.taxonomy import TAGGING_VERSION
from src.web.auth import ensure_csrf_token, get_current_user
from src.web.common import (
    check_rate_limit,
    ctx,
    format_ts,
    hx_target,
    is_htmx_request,
    require_login_and_csrf,
    template_response,
    toast,
)
from src.web.jobs_ui import render_job_cards, start_background_job
from src.web.middleware import RequestLoggingMiddleware
from src.web.routes.auth import router as auth_router
from src.web.routes.calendar import router as calendar_router
from src.web.routes.jobs import router as jobs_router
from src.web.routes.profile import router as profile_router
from src.web.routes.sources import router as sources_router

db = create_database()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

logger = logging.getLogger("uvicorn.error")

_rate_limit_store: dict[str, deque[float]] = {}
_bulk_unattend_undo_store: dict[str, list[str]] = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    await cast(Database, app.state.db).connect()
    await cast(Database, app.state.db).fail_stale_jobs(
        max_age_seconds=settings.background_job_timeout_seconds
    )
    yield
    await cast(Database, app.state.db).close()


app = FastAPI(title="Family Events", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.add_middleware(cast(Any, RequestLoggingMiddleware))
if not settings.session_secret:
    raise RuntimeError("SESSION_SECRET must be set (in .env) before starting the web app")

app.add_middleware(
    cast(Any, SessionMiddleware),
    secret_key=settings.session_secret,
    session_cookie="session",
    max_age=max(300, settings.session_max_age_seconds),
    same_site=settings.session_cookie_same_site,
    https_only=settings.session_cookie_secure,
    domain=settings.session_cookie_domain or None,
)
app.state.db = db
app.state.templates = templates
app.state.rate_limit_store = _rate_limit_store
app.state.bulk_unattend_undo_store = _bulk_unattend_undo_store


app.include_router(auth_router)
app.include_router(profile_router)
app.include_router(sources_router)
app.include_router(jobs_router)
app.include_router(calendar_router)


# ----- Pages -----


@app.get("/health", response_class=JSONResponse)
async def health_check() -> JSONResponse:
    """Simple health probe for service/process monitors."""
    db_ok = False
    event_count: int | None = None
    latest_scrape_at: datetime | None = None

    try:
        stats = await db.health_stats()
        event_count = int(stats["event_count"])
        latest_scrape_at = stats["latest_scraped_at"]
        db_ok = True
    except Exception as exc:
        logger.exception("health_check_db_failed: %s", exc)

    status = "ok" if db_ok else "degraded"
    payload = {
        "status": status,
        "service": "family-events",
        "time": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "checks": {
            "database": {
                "ok": db_ok,
                "event_count": event_count,
                "latest_scraped_at": format_ts(latest_scrape_at),
            }
        },
    }
    return JSONResponse(payload, status_code=200 if db_ok else 503)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    events = await db.get_recent_events(days=30)
    total = len(events)
    tagged = sum(1 for event in events if event.tags)
    untagged = total - tagged
    stale_tagged = await db.count_stale_tagged_events(tagging_version=TAGGING_VERSION)
    sources = len(set(event.source for event in events))
    timestamps = await db.get_pipeline_timestamps()
    user = await get_current_user(request, db)
    recent_jobs = await db.list_jobs(owner_user_id=user.id, limit=8) if user else []
    recent_job_cards = render_job_cards(
        recent_jobs,
        target_prefix="job-history-",
        refresh_path="/",
        refresh_select="#section-jobs",
        refresh_target_id="section-jobs",
    )

    top_events = sorted(
        [event for event in events if event.tags],
        key=lambda event: event.tags.toddler_score,
        reverse=True,
    )[:5]
    arts_events = sorted(
        [event for event in events if event.tags and "arts" in (event.tags.categories or [])],
        key=lambda event: event.tags.toddler_score,
        reverse=True,
    )[:8]
    outdoor_events = sorted(
        [
            event
            for event in events
            if event.tags and event.tags.indoor_outdoor in ("outdoor", "both")
        ],
        key=lambda event: event.tags.toddler_score,
        reverse=True,
    )[:8]
    nature_events = sorted(
        [event for event in events if event.tags and "nature" in (event.tags.categories or [])],
        key=lambda event: event.tags.toddler_score,
        reverse=True,
    )[:8]

    near_city = user.home_city if user and user.home_city else "Your City"
    near_you_events = sorted(
        [event for event in events if event.tags and event.location_city == near_city],
        key=lambda event: event.tags.toddler_score,
        reverse=True,
    )[:8]

    return template_response(
        request,
        "dashboard.html",
        await ctx(
            request,
            active_page="discover",
            total=total,
            tagged=tagged,
            untagged=untagged,
            stale_tagged=stale_tagged,
            sources=sources,
            last_scraped_at=timestamps["last_scraped_at"],
            last_tagged_at=timestamps["last_tagged_at"],
            top_events=top_events,
            near_city=near_city,
            near_you_events=near_you_events,
            arts_events=arts_events,
            outdoor_events=outdoor_events,
            nature_events=nature_events,
            recent_jobs=recent_jobs,
            recent_job_cards=recent_job_cards,
        ),
    )


@app.get("/events", response_class=HTMLResponse)
async def events_page(
    request: Request,
    q: str = "",
    city: str = "",
    source: str = "",
    tagged: str = "",
    attended: str = "",
    score_min: str = "",
    sort: str = "start_time",
    page: int = 1,
):
    per_page = 25
    score_min_int = int(score_min) if score_min.isdigit() else None
    events, total = await db.search_events(
        days=30,
        q=q,
        city=city,
        source=source,
        tagged=tagged,
        attended=attended,
        score_min=score_min_int,
        sort=sort,
        page=page,
        per_page=per_page,
    )
    total_pages = max(1, (total + per_page - 1) // per_page)
    filters = await db.get_filter_options()
    active_page = "attended" if attended == "yes" else "events"

    page_ctx = await ctx(
        request,
        active_page=active_page,
        events=events,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        q=q,
        city=city,
        source=source,
        tagged=tagged,
        attended=attended,
        score_min=score_min_int,
        sort=sort,
        cities=filters["cities"],
        sources=filters["sources"],
    )

    if is_htmx_request(request) and hx_target(request) == "events-results":
        return template_response(request, "partials/_events_table.html", page_ctx)
    return template_response(request, "events.html", page_ctx)


@app.get("/event/{event_id}", response_class=HTMLResponse)
async def event_detail(request: Request, event_id: str):
    event = await db.get_event(event_id)
    if not event:
        return templates.TemplateResponse(
            "base.html",
            {"request": request, "content": "Event not found."},
            status_code=404,
        )
    raw_data = json.dumps(event.raw_data, indent=2, default=str)[:3000]

    map_query = ", ".join(
        [
            value
            for value in [event.location_name, event.location_address, event.location_city]
            if value
        ]
    )
    maps_url = (
        f"https://www.google.com/maps/search/?api=1&query={quote_plus(map_query)}"
        if map_query
        else None
    )

    related_events: list[tuple[object, float]] = []
    score_breakdown: dict[str, float] | None = None
    if event.tags:
        user = await get_current_user(request, db)
        profile = user.interest_profile if user else InterestProfile()

        start = event.start_time.date()
        weather = await WeatherService().get_weekend_forecast(start, start)
        if event.score_breakdown:
            score_breakdown = event.score_breakdown
        else:
            breakdown = score_event_breakdown(event, profile, weather)
            score_breakdown = {
                "final": breakdown.final,
                "toddler_fit": breakdown.toddler_fit,
                "intrinsic": breakdown.intrinsic,
                "interest": breakdown.interest,
                "weather": breakdown.weather,
                "city": breakdown.city,
                "timing": breakdown.timing,
                "logistics": breakdown.logistics,
                "novelty": breakdown.novelty,
                "confidence": breakdown.confidence,
                "rule_penalty": breakdown.rule_penalty,
                "budget_penalty": breakdown.budget_penalty,
            }

        candidates = await db.get_recent_events(days=30)
        related = [
            candidate
            for candidate in candidates
            if candidate.id != event.id
            and candidate.tags
            and candidate.location_city == event.location_city
            and abs((candidate.start_time - event.start_time).days) <= 14
        ]
        related.sort(
            key=lambda candidate: candidate.tags.toddler_score if candidate.tags else 0,
            reverse=True,
        )
        related_events = [
            (candidate, float(candidate.tags.toddler_score if candidate.tags else 0))
            for candidate in related[:4]
        ]

    return template_response(
        request,
        "event_detail.html",
        await ctx(
            request,
            active_page="events",
            event=event,
            raw_data=raw_data,
            maps_url=maps_url,
            related_events=related_events,
            score_breakdown=score_breakdown,
        ),
    )


@app.get("/weekend", response_class=HTMLResponse)
async def weekend_page(request: Request):
    today = date.today()
    days_until_sat = (5 - today.weekday()) % 7
    saturday = today + timedelta(days=days_until_sat)
    sunday = saturday + timedelta(days=1)

    weather = await WeatherService().get_weekend_forecast(saturday, sunday)
    events = await db.get_events_for_weekend(saturday.isoformat(), sunday.isoformat())

    tagged = [event for event in events if event.tags]
    untagged_count = len(events) - len(tagged)
    user = await get_current_user(request, db)
    profile = user.interest_profile if user else InterestProfile()
    child_name = user.child_name if user else "Your Little One"
    ranked = rank_events(tagged, profile, weather)
    message = format_console_message(ranked, weather, child_name) if ranked else ""
    weather_summary, weather_tone, weather_tips = summarize_weekend_recommendation(weather)

    return template_response(
        request,
        "weekend.html",
        await ctx(
            request,
            active_page="weekend",
            saturday=saturday,
            sunday=sunday,
            weather=weather,
            ranked=ranked,
            weekend_event_count=len(events),
            untagged_weekend_event_count=untagged_count,
            message=message,
            weather_summary=weather_summary,
            weather_tone=weather_tone,
            weather_tips=weather_tips,
        ),
    )


# ----- API Endpoints -----


@app.post("/api/scrape", response_class=HTMLResponse)
async def api_scrape(request: Request):
    user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := check_rate_limit(request, "api_scrape"):
        return throttled

    from src.scheduler import run_scrape

    database_url = db.database_url

    async def runner(_job) -> int:
        async with create_database(database_url=database_url) as job_db:
            return await run_scrape(job_db)

    return await start_background_job(
        request,
        user=user,
        kind="scrape",
        key="pipeline:scrape",
        label="Scrape job",
        runner=runner,
        target_id="dashboard-job-status",
    )


@app.post("/api/tag", response_class=HTMLResponse)
async def api_tag(request: Request):
    user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := check_rate_limit(request, "api_tag"):
        return throttled

    from src.scheduler import run_tag

    database_url = db.database_url

    async def runner(job) -> int:
        async with create_database(database_url=database_url) as job_db:
            await job.update(detail="Preparing tag batches…", result={"processed": 0, "total": 0, "succeeded": 0, "failed": 0})
            return await run_tag(
                job_db,
                include_stale=False,
                progress_callback=lambda progress: job.update(
                    detail=progress.get("summary", "Running…"), result=progress
                ),
            )

    return await start_background_job(
        request,
        user=user,
        kind="tag",
        key="pipeline:tag",
        label="Tag job",
        runner=runner,
        target_id="dashboard-job-status",
    )


@app.post("/api/dedupe", response_class=HTMLResponse)
async def api_dedupe(request: Request):
    user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := check_rate_limit(request, "api_dedupe"):
        return throttled

    database_url = db.database_url

    async def runner(_job) -> int:
        async with create_database(database_url=database_url) as job_db:
            result = await job_db.dedupe_existing_events()
            return int(result["merged"])

    return await start_background_job(
        request,
        user=user,
        kind="dedupe",
        key="pipeline:dedupe",
        label="Dedupe job",
        runner=runner,
        target_id="dashboard-job-status",
    )


@app.post("/api/notify", response_class=HTMLResponse)
async def api_notify(request: Request):
    user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := check_rate_limit(request, "api_notify"):
        return throttled

    from src.scheduler import run_notify

    database_url = db.database_url

    async def runner(_job) -> str:
        async with create_database(database_url=database_url) as job_db:
            return await run_notify(job_db, user=user)

    return await start_background_job(
        request,
        user=user,
        kind="notify",
        key=f"pipeline:notify:{user.id}",
        label="Notification job",
        runner=runner,
        target_id="dashboard-job-status",
    )


def _render_event_attendance(request: Request, event, *, target_id: str) -> str:
    return templates.get_template("partials/_event_attendance.html").render(
        request=request,
        event=event,
        csrf_token=ensure_csrf_token(request),
        target_id=target_id,
    )


@app.post("/api/attend/{event_id}", response_class=HTMLResponse)
async def api_attend(request: Request, event_id: str):
    user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := check_rate_limit(request, "api_attend"):
        return throttled

    await db.mark_attended(event_id)
    event = await db.get_event(event_id)
    if event is None:
        raise ValueError("Event disappeared after attend")
    target_id = request.query_params.get("target_id", "event-attendance")
    return toast("Marked attended", body=_render_event_attendance(request, event, target_id=target_id))


@app.post("/api/unattend/{event_id}", response_class=HTMLResponse)
async def api_unattend(request: Request, event_id: str):
    user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := check_rate_limit(request, "api_unattend"):
        return throttled

    await db.set_attended(event_id, attended=False)
    event = await db.get_event(event_id)
    if event is None:
        raise ValueError("Event disappeared after unattend")
    target_id = request.query_params.get("target_id", "event-attendance")
    return toast("Marked as not attended", body=_render_event_attendance(request, event, target_id=target_id))


@app.post("/api/unattend-bulk", response_class=HTMLResponse)
async def api_unattend_bulk(request: Request):
    user, form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None and form is not None
    if throttled := check_rate_limit(request, "api_unattend_bulk"):
        return throttled

    event_ids = [str(event_id) for event_id in form.getlist("event_ids") if str(event_id).strip()]
    if not event_ids:
        return toast("Select at least one event", "warning", status_code=422)

    await db.set_attended_bulk(event_ids, attended=False)

    undo_token = str(uuid4())
    _bulk_unattend_undo_store[undo_token] = event_ids
    payload = json.dumps(
        {
            "showToast": {
                "message": f"Updated {len(event_ids)} event(s)",
                "variant": "success",
                "undo": {"path": f"/api/unattend-bulk/undo/{undo_token}", "label": "Undo"},
            }
        }
    )
    return HTMLResponse(content="", status_code=200, headers={"HX-Trigger": payload})


@app.post("/api/unattend-bulk/undo/{undo_token}", response_class=HTMLResponse)
async def api_unattend_bulk_undo(request: Request, undo_token: str):
    user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := check_rate_limit(request, "api_unattend_bulk_undo"):
        return throttled

    event_ids = _bulk_unattend_undo_store.pop(undo_token, [])
    if not event_ids:
        return toast("Nothing to undo", "warning")

    await db.set_attended_bulk(event_ids, attended=True)
    return toast(f"Restored {len(event_ids)} event(s)")


@app.get("/api/events")
async def api_events():
    events = await db.get_recent_events(days=30)
    return [
        {
            "id": event.id,
            "title": event.title,
            "source": event.source,
            "city": event.location_city,
            "start_time": event.start_time.isoformat(),
            "tagged": event.tags is not None,
            "toddler_score": event.tags.toddler_score if event.tags else None,
        }
        for event in events
    ]

@app.exception_handler(404)
async def not_found_handler(request: Request, _exc: Exception) -> HTMLResponse:
    """Render friendly 404 page for missing routes."""
    return template_response(request, "404.html", await ctx(request), status_code=404)


@app.exception_handler(500)
async def server_error_handler(request: Request, _exc: Exception) -> HTMLResponse:
    """Render friendly 500 page for unhandled server errors."""
    return template_response(request, "500.html", await ctx(request), status_code=500)


@app.post("/api/tag/stale", response_class=HTMLResponse)
async def api_tag_stale(request: Request):
    user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := check_rate_limit(request, "api_tag_stale"):
        return throttled

    from src.scheduler import run_tag

    database_url = db.database_url

    async def runner(job) -> int:
        async with create_database(database_url=database_url) as job_db:
            stale_count = await job_db.count_stale_tagged_events(tagging_version=TAGGING_VERSION)
            await job.update(
                detail="Preparing stale retag batches…",
                result={
                    "processed": 0,
                    "total": stale_count,
                    "succeeded": 0,
                    "failed": 0,
                    "summary": f"0/{stale_count} processed · 0 tagged · 0 failed",
                },
            )
            return await run_tag(
                job_db,
                include_stale=True,
                progress_callback=lambda progress: job.update(
                    detail=progress.get("summary", "Running…"), result=progress
                ),
            )

    return await start_background_job(
        request,
        user=user,
        kind="tag",
        key="pipeline:tag:stale",
        label="Retag stale events",
        runner=runner,
        target_id="dashboard-job-status",
    )
