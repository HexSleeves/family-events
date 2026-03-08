"""FastAPI web admin for Family Events."""

from __future__ import annotations

import json
import logging
from collections import deque
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote_plus
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from src.config import settings
from src.db.database import Database
from src.db.models import InterestProfile
from src.notifications.formatter import format_console_message
from src.ranker.scoring import (
    _city_score,
    _interest_score,
    _logistics_score,
    _timing_score,
    _weather_score,
    rank_events,
)
from src.ranker.weather import WeatherService, summarize_weekend_recommendation
from src.web.auth import get_current_user
from src.web.common import check_rate_limit, ctx, format_ts, require_login_and_csrf, toast
from src.web.jobs_ui import job_template_context, start_background_job
from src.web.middleware import RequestLoggingMiddleware
from src.web.routes.auth import router as auth_router
from src.web.routes.profile import router as profile_router
from src.web.routes.sources import router as sources_router

db = Database()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

logger = logging.getLogger("uvicorn.error")

_rate_limit_store: dict[str, deque[float]] = {}
_bulk_unattend_undo_store: dict[str, list[str]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await cast(Database, app.state.db).connect()
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


# ----- Pages -----


@app.get("/health", response_class=JSONResponse)
async def health_check() -> JSONResponse:
    """Simple health probe for service/process monitors."""
    db_ok = False
    event_count: int | None = None
    latest_scrape_at: datetime | None = None

    try:
        async with db.db.execute(
            "SELECT COUNT(*) as n, MAX(scraped_at) as latest FROM events"
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                event_count = int(row["n"]) if row["n"] is not None else 0
                latest = row["latest"]
                latest_scrape_at = datetime.fromisoformat(str(latest)) if latest else None
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
    sources = len(set(event.source for event in events))
    timestamps = await db.get_pipeline_timestamps()
    user = await get_current_user(request, db)
    recent_jobs = await db.list_jobs(owner_user_id=user.id, limit=8) if user else []

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

    near_city = user.home_city if user else "Lafayette"
    near_you_events = sorted(
        [event for event in events if event.tags and event.location_city == near_city],
        key=lambda event: event.tags.toddler_score,
        reverse=True,
    )[:8]

    return templates.TemplateResponse(
        "dashboard.html",
        await ctx(
            request,
            active_page="discover",
            total=total,
            tagged=tagged,
            untagged=untagged,
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

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("partials/_events_table.html", page_ctx)
    return templates.TemplateResponse("events.html", page_ctx)


@app.get("/event/{event_id}", response_class=HTMLResponse)
async def event_detail(request: Request, event_id: str):
    from src.db.database import _row_to_event

    async with db.db.execute("SELECT * FROM events WHERE id = :id", {"id": event_id}) as cursor:
        row = await cursor.fetchone()
    if not row:
        return templates.TemplateResponse(
            "base.html",
            {"request": request, "content": "Event not found."},
            status_code=404,
        )

    event = _row_to_event(row)
    raw_data = json.dumps(event.raw_data, indent=2, default=str)[:3000]

    map_query = ", ".join(
        [value for value in [event.location_name, event.location_address, event.location_city] if value]
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
        tags = event.tags
        score_breakdown = {
            "toddler": tags.toddler_score * 3.0,
            "interest": _interest_score(tags.categories, profile) * 2.5,
            "weather": _weather_score(event, tags, weather) * 2.0,
            "city": _city_score(event, profile) * 2.0,
            "timing": _timing_score(event, profile) * 1.5,
            "logistics": _logistics_score(tags) * 1.0,
            "novelty": (5.0 if not event.attended else 0.0) * 0.5,
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

    return templates.TemplateResponse(
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


@app.get("/calendar", response_class=HTMLResponse)
async def calendar_page(request: Request, month: str = "", attended: str = ""):
    today = datetime.now(tz=UTC).date()
    if month:
        try:
            month_date = datetime.strptime(month, "%Y-%m").replace(tzinfo=UTC).date()
            month_start = month_date.replace(day=1)
        except ValueError:
            month_start = today.replace(day=1)
    else:
        month_start = today.replace(day=1)

    if month_start.month == 12:
        next_month_start = month_start.replace(year=month_start.year + 1, month=1, day=1)
    else:
        next_month_start = month_start.replace(month=month_start.month + 1, day=1)

    prev_month = (
        month_start.replace(year=month_start.year - 1, month=12, day=1)
        if month_start.month == 1
        else month_start.replace(month=month_start.month - 1, day=1)
    )

    events = await db.get_events_between(
        datetime.combine(month_start, datetime.min.time(), tzinfo=UTC),
        datetime.combine(next_month_start, datetime.min.time(), tzinfo=UTC),
        attended=attended,
    )

    events_by_day: dict[str, list[Any]] = {}
    for event in events:
        key = event.start_time.date().isoformat()
        events_by_day.setdefault(key, []).append(event)

    first_weekday = month_start.weekday()
    grid_start = month_start - timedelta(days=first_weekday)
    days: list[dict[str, Any]] = []
    for offset in range(42):
        day = grid_start + timedelta(days=offset)
        key = day.isoformat()
        days.append(
            {
                "date": day,
                "key": key,
                "in_month": day.month == month_start.month,
                "is_today": day == today,
                "events": events_by_day.get(key, []),
            }
        )

    page_ctx = await ctx(
        request,
        active_page="calendar",
        month_start=month_start,
        prev_month=prev_month,
        next_month=next_month_start,
        attended=attended,
        total_events=len(events),
        days=days,
    )

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("partials/_calendar_grid.html", page_ctx)
    return templates.TemplateResponse("calendar.html", page_ctx)


@app.get("/calendar.ics")
async def calendar_ics(request: Request, month: str = "", attended: str = ""):
    today = datetime.now(tz=UTC).date()
    if month:
        try:
            month_date = datetime.strptime(month, "%Y-%m").replace(tzinfo=UTC).date()
            month_start = month_date.replace(day=1)
        except ValueError:
            month_start = today.replace(day=1)
    else:
        month_start = today.replace(day=1)

    if month_start.month == 12:
        next_month_start = month_start.replace(year=month_start.year + 1, month=1, day=1)
    else:
        next_month_start = month_start.replace(month=month_start.month + 1, day=1)

    events = await db.get_events_between(
        datetime.combine(month_start, datetime.min.time(), tzinfo=UTC),
        datetime.combine(next_month_start, datetime.min.time(), tzinfo=UTC),
        attended=attended,
    )

    def esc(value: str) -> str:
        return (
            value.replace("\\", "\\\\")
            .replace(";", "\\;")
            .replace(",", "\\,")
            .replace("\n", "\\n")
        )

    out = StringIO()
    out.write("BEGIN:VCALENDAR\r\n")
    out.write("VERSION:2.0\r\n")
    out.write("PRODID:-//Family Events//Calendar Export//EN\r\n")
    out.write("CALSCALE:GREGORIAN\r\n")
    generated = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")

    for event in events:
        start = event.start_time.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        end_dt = event.end_time or (event.start_time + timedelta(hours=2))
        end = end_dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        out.write("BEGIN:VEVENT\r\n")
        out.write(f"UID:{event.id}@family-events\r\n")
        out.write(f"DTSTAMP:{generated}\r\n")
        out.write(f"DTSTART:{start}\r\n")
        out.write(f"DTEND:{end}\r\n")
        out.write(f"SUMMARY:{esc(event.title)}\r\n")
        location = ", ".join(
            [value for value in [event.location_name, event.location_address, event.location_city] if value]
        )
        if location:
            out.write(f"LOCATION:{esc(location)}\r\n")
        if event.description:
            out.write(f"DESCRIPTION:{esc(event.description)}\r\n")
        out.write(f"URL:{esc(event.source_url)}\r\n")
        out.write("END:VEVENT\r\n")

    out.write("END:VCALENDAR\r\n")
    filename = f"family-events-{month_start.strftime('%Y-%m')}.ics"
    return Response(
        content=out.getvalue(),
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/weekend", response_class=HTMLResponse)
async def weekend_page(request: Request):
    today = date.today()
    days_until_sat = (5 - today.weekday()) % 7
    saturday = today + timedelta(days=days_until_sat)
    sunday = saturday + timedelta(days=1)

    weather = await WeatherService().get_weekend_forecast(saturday, sunday)
    events = await db.get_events_for_weekend(saturday.isoformat(), sunday.isoformat())
    if not events:
        events = await db.get_recent_events(days=14)

    tagged = [event for event in events if event.tags]
    user = await get_current_user(request, db)
    profile = user.interest_profile if user else InterestProfile()
    child_name = user.child_name if user else "Your Little One"
    ranked = rank_events(tagged, profile, weather)
    message = format_console_message(ranked, weather, child_name)
    weather_summary, weather_tone, weather_tips = summarize_weekend_recommendation(weather)

    return templates.TemplateResponse(
        "weekend.html",
        await ctx(
            request,
            active_page="weekend",
            saturday=saturday,
            sunday=sunday,
            weather=weather,
            ranked=ranked,
            message=message,
            weather_summary=weather_summary,
            weather_tone=weather_tone,
            weather_tips=weather_tips,
        ),
    )


# ----- API Endpoints -----


@app.get("/api/jobs/{job_id}", response_class=HTMLResponse)
async def api_job_status(request: Request, job_id: str, target_id: str = "job-status"):
    user = await get_current_user(request, db)
    if not user:
        return HTMLResponse("", status_code=401)

    job = await db.get_job(job_id)
    if not job or job.owner_user_id != user.id:
        return HTMLResponse("", status_code=404)

    return templates.TemplateResponse(
        "partials/_job_status.html",
        {"request": request, **job_template_context(job, target_id=target_id)},
    )


@app.post("/api/scrape", response_class=HTMLResponse)
async def api_scrape(request: Request):
    user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := check_rate_limit(request, "api_scrape"):
        return throttled

    from src.scheduler import run_scrape

    db_path = db.db_path

    async def runner() -> int:
        async with Database(db_path) as job_db:
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

    db_path = db.db_path

    async def runner() -> int:
        async with Database(db_path) as job_db:
            return await run_tag(job_db)

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

    db_path = db.db_path

    async def runner() -> int:
        async with Database(db_path) as job_db:
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

    db_path = db.db_path

    async def runner() -> str:
        async with Database(db_path) as job_db:
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


@app.post("/api/attend/{event_id}", response_class=HTMLResponse)
async def api_attend(request: Request, event_id: str):
    user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := check_rate_limit(request, "api_attend"):
        return throttled

    await db.mark_attended(event_id)
    return toast("Marked attended")


@app.post("/api/unattend/{event_id}", response_class=HTMLResponse)
async def api_unattend(request: Request, event_id: str):
    user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := check_rate_limit(request, "api_unattend"):
        return throttled

    await db.db.execute("UPDATE events SET attended = 0 WHERE id = :id", {"id": event_id})
    await db.db.commit()
    return toast("Marked as not attended")


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
        return toast("Select at least one event", "warning")

    await db.db.executemany(
        "UPDATE events SET attended = 0 WHERE id = ?",
        [(event_id,) for event_id in event_ids],
    )
    await db.db.commit()

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

    await db.db.executemany(
        "UPDATE events SET attended = 1 WHERE id = ?",
        [(event_id,) for event_id in event_ids],
    )
    await db.db.commit()
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
    return templates.TemplateResponse("404.html", await ctx(request), status_code=404)


@app.exception_handler(500)
async def server_error_handler(request: Request, _exc: Exception) -> HTMLResponse:
    """Render friendly 500 page for unhandled server errors."""
    return templates.TemplateResponse("500.html", await ctx(request), status_code=500)
