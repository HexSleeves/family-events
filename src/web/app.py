"""FastAPI web admin for Family Events."""

from __future__ import annotations

import logging
from collections import deque
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from src.config import settings
from src.db.database import Database, create_database
from src.db.models import InterestProfile
from src.notifications.formatter import format_console_message
from src.ranker.scoring import rank_events
from src.ranker.weather import WeatherService, summarize_weekend_recommendation
from src.tagger.taxonomy import TAGGING_VERSION
from src.web.auth import get_current_user
from src.web.common import ctx, format_ts, template_response
from src.web.jobs_ui import render_job_cards
from src.web.middleware import RequestLoggingMiddleware
from src.web.routes.auth import router as auth_router
from src.web.routes.calendar import router as calendar_router
from src.web.routes.events import router as events_router
from src.web.routes.jobs import router as jobs_router
from src.web.routes.pipeline import router as pipeline_router
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
app.include_router(events_router)
app.include_router(pipeline_router)


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

@app.exception_handler(404)
async def not_found_handler(request: Request, _exc: Exception) -> HTMLResponse:
    """Render friendly 404 page for missing routes."""
    return template_response(request, "404.html", await ctx(request), status_code=404)


@app.exception_handler(500)
async def server_error_handler(request: Request, _exc: Exception) -> HTMLResponse:
    """Render friendly 500 page for unhandled server errors."""
    return template_response(request, "500.html", await ctx(request), status_code=500)
