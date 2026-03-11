"""Top-level page and health routes."""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src.db.models import InterestProfile
from src.notifications.formatter import format_console_message
from src.ranker.scoring import rank_events
from src.ranker.weather import WeatherService, summarize_weekend_recommendation
from src.tagger.taxonomy import TAGGING_VERSION
from src.timezones import current_weekend_dates, utc_now
from src.web.auth import get_current_user
from src.web.common import ctx, format_ts, get_db, template_response
from src.web.jobs_ui import render_job_cards

router = APIRouter()
logger = logging.getLogger("uvicorn.error")


@router.get("/health", response_class=JSONResponse)
async def health_check(request: Request) -> JSONResponse:
    """Simple health probe for service/process monitors."""
    db = get_db(request)
    db_ok = False
    stats: dict[str, object] = {}

    try:
        stats = await db.health_stats()
        db_ok = True
    except Exception as exc:
        logger.exception("health_check_db_failed: %s", exc)

    event_count_raw = stats.get("event_count")
    event_count = event_count_raw if isinstance(event_count_raw, int) else 0
    latest_scrape_at = stats.get("latest_scraped_at")
    latest_tagged_at = stats.get("latest_tagged_at")
    latest_notified_at = stats.get("latest_notified_at")
    stuck_running_jobs_raw = stats.get("stuck_running_jobs")
    stuck_running_jobs = stuck_running_jobs_raw if isinstance(stuck_running_jobs_raw, int) else 0

    status = "ok" if db_ok and stuck_running_jobs == 0 else "degraded"
    payload = {
        "status": status,
        "service": "family-events",
        "time": utc_now().isoformat().replace("+00:00", "Z"),
        "checks": {
            "database": {
                "ok": db_ok,
                "event_count": event_count,
            },
            "pipeline": {
                "latest_scraped_at": format_ts(
                    latest_scrape_at if isinstance(latest_scrape_at, datetime) else None
                ),
                "latest_tagged_at": format_ts(
                    latest_tagged_at if isinstance(latest_tagged_at, datetime) else None
                ),
                "latest_notified_at": format_ts(
                    latest_notified_at if isinstance(latest_notified_at, datetime) else None
                ),
                "stuck_running_jobs": stuck_running_jobs,
            },
        },
    }
    return JSONResponse(payload, status_code=200 if status == "ok" else 503)


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    db = get_db(request)
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


@router.get("/weekend", response_class=HTMLResponse)
async def weekend_page(request: Request):
    db = get_db(request)
    saturday, sunday = current_weekend_dates()

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
