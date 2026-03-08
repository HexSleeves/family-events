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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from src.config import settings
from src.db.database import Database
from src.db.models import Constraints, InterestProfile, User
from src.notifications.formatter import format_console_message
from src.ranker.scoring import rank_events, score_event_breakdown
from src.ranker.weather import WeatherService, summarize_weekend_recommendation
from src.web.auth import (
    ensure_csrf_token,
    get_current_user,
    hash_password,
    login_session,
    logout_session,
    rotate_csrf_token,
    validate_password,
    verify_password,
)
from src.web.common import (
    change_theme,
    check_rate_limit,
    ctx,
    format_ts,
    null_response,
    require_csrf,
    require_login_and_csrf,
    toast,
)
from src.web.jobs import job_registry
from src.web.jobs_ui import job_template_context, render_job_cards, start_background_job
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


# ----- Auth Pages -----


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = await get_current_user(request, db)
    if user:
        return RedirectResponse("/profile", status_code=302)
    return templates.TemplateResponse("login.html", await ctx(request, active_page="auth"))


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request):
    form, denied = await require_csrf(request)
    if denied:
        return denied
    if throttled := check_rate_limit(
        request,
        "login_submit",
        limit=settings.auth_rate_limit_max_requests,
        window=settings.auth_rate_limit_window_seconds,
        message="Too many login attempts. Try again later.",
    ):
        return throttled

    assert form is not None
    email = str(form.get("email", "")).strip().lower()
    password = str(form.get("password", ""))

    user = await db.get_user_by_email(email)
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "login.html",
            {**await ctx(request, active_page="auth"), "error": "Invalid email or password."},
        )

    login_session(request, user)
    rotate_csrf_token(request)
    return RedirectResponse("/profile", status_code=302)


@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    user = await get_current_user(request, db)
    if user:
        return RedirectResponse("/profile", status_code=302)
    return templates.TemplateResponse("signup.html", await ctx(request, active_page="auth"))


@app.post("/signup", response_class=HTMLResponse)
async def signup_submit(request: Request):
    form, denied = await require_csrf(request)
    if denied:
        return denied
    if throttled := check_rate_limit(
        request,
        "signup_submit",
        limit=settings.auth_rate_limit_max_requests,
        window=settings.auth_rate_limit_window_seconds,
        message="Too many signup attempts. Try again later.",
    ):
        return throttled

    assert form is not None
    email = str(form.get("email", "")).strip().lower()
    display_name = str(form.get("display_name", "")).strip()
    password = str(form.get("password", ""))
    confirm = str(form.get("confirm_password", ""))

    errors: list[str] = []
    if not email or "@" not in email:
        errors.append("Valid email is required.")
    if not display_name:
        errors.append("Display name is required.")
    errors.extend(validate_password(password))
    if password != confirm:
        errors.append("Passwords don't match.")
    if not errors and await db.get_user_by_email(email):
        errors.append("An account with this email already exists.")

    if errors:
        return templates.TemplateResponse(
            "signup.html",
            {
                **await ctx(request, active_page="auth"),
                "errors": errors,
                "email": email,
                "display_name": display_name,
            },
        )

    user = User(
        email=email,
        display_name=display_name,
        password_hash=hash_password(password),
    )
    await db.create_user(user)
    login_session(request, user)
    rotate_csrf_token(request)
    return RedirectResponse("/profile", status_code=302)


@app.post("/logout")
async def logout(request: Request):
    _user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    logout_session(request)
    return RedirectResponse("/", status_code=302)


# ----- Profile Page -----


@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    user = await get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    sources = await db.get_user_sources(user.id)
    return templates.TemplateResponse(
        "profile.html",
        {**await ctx(request, active_page="profile"), "sources": sources},
    )


@app.post("/api/profile/location", response_class=HTMLResponse)
async def api_update_location(request: Request):
    user, form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None and form is not None
    home_city = str(form.get("home_city", "Lafayette")).strip()
    pref_raw = str(form.get("preferred_cities", "")).strip()
    preferred = [c.strip() for c in pref_raw.split(",") if c.strip()]
    if not preferred:
        preferred = [home_city]
    await db.update_user(user.id, home_city=home_city, preferred_cities=preferred)
    return toast("Location updated")


@app.post("/api/profile/preferences", response_class=HTMLResponse)
async def api_update_preferences(request: Request):
    user, form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None and form is not None
    loves = [x.strip() for x in str(form.get("loves", "")).split(",") if x.strip()]
    likes = [x.strip() for x in str(form.get("likes", "")).split(",") if x.strip()]
    dislikes = [x.strip() for x in str(form.get("dislikes", "")).split(",") if x.strip()]
    nap_time = str(form.get("nap_time", "13:00-15:00")).strip()
    bedtime = str(form.get("bedtime", "19:30")).strip()
    budget = float(str(form.get("budget", "30.0")))
    max_drive = int(str(form.get("max_drive", "45")))

    profile = InterestProfile(
        loves=loves or user.interest_profile.loves,
        likes=likes or user.interest_profile.likes,
        dislikes=dislikes or user.interest_profile.dislikes,
        constraints=Constraints(
            max_drive_time_minutes=max_drive,
            preferred_cities=user.preferred_cities,
            home_city=user.home_city,
            nap_time=nap_time,
            bedtime=bedtime,
            budget_per_event=budget,
        ),
    )
    await db.update_user(user.id, interest_profile=profile)
    return toast("Preferences updated")


@app.post("/api/profile/theme", response_class=HTMLResponse)
async def api_update_theme(request: Request):
    user, form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None and form is not None
    user_theme = user.theme
    theme = str(form.get("theme", user_theme)).strip()
    if theme == user_theme:
        return null_response()
    if theme not in ("light", "dark", "auto"):
        theme = "auto"
    await db.update_user(user.id, theme=theme)
    return change_theme(theme)


@app.post("/api/profile/notifications", response_class=HTMLResponse)
async def api_update_notifications(request: Request):
    user, form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None and form is not None
    channels = form.getlist("channels")
    if not channels:
        channels = ["console"]
    email_to = str(form.get("email_to", "")).strip()
    sms_to = str(form.get("sms_to", "")).strip()
    child_name = str(form.get("child_name", "")).strip() or "Your Little One"
    if "email" in channels and not email_to:
        return toast("Add a notification email to enable email delivery", "error")
    if "sms" in channels and not sms_to:
        return toast("Add a phone number to enable SMS delivery", "error")
    await db.update_user(
        user.id,
        notification_channels=[str(c) for c in channels],
        email_to=email_to,
        sms_to=sms_to,
        child_name=child_name,
    )
    return toast("Notification settings updated")


@app.post("/api/profile/password", response_class=HTMLResponse)
async def api_update_password(request: Request):
    user, form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None and form is not None
    current = str(form.get("current_password", ""))
    new_pw = str(form.get("new_password", ""))
    confirm = str(form.get("confirm_password", ""))
    if not verify_password(current, user.password_hash):
        return toast("Current password is incorrect", "error")
    password_errors = validate_password(new_pw)
    if password_errors:
        return toast(password_errors[0], "error")
    if new_pw != confirm:
        return toast("Passwords don't match", "error")
    await db.update_user(user.id, password_hash=hash_password(new_pw))
    return toast("Password changed")


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
        breakdown = score_event_breakdown(event, profile, weather)
        score_breakdown = {
            "toddler": breakdown.toddler_fit,
            "intrinsic": breakdown.intrinsic,
            "interest": breakdown.interest,
            "weather": breakdown.weather,
            "city": breakdown.city,
            "timing": breakdown.timing,
            "logistics": breakdown.logistics,
            "novelty": breakdown.novelty,
            "confidence": breakdown.confidence,
            "rule_penalty": -breakdown.rule_penalty,
            "budget_penalty": -breakdown.budget_penalty,
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
@app.get("/calendars", response_class=HTMLResponse)
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
    events.sort(
        key=lambda event: (
            event.start_time.astimezone(UTC)
            if event.start_time.tzinfo is not None
            else event.start_time.replace(tzinfo=UTC)
        )
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
        day_events = events_by_day.get(key, [])
        days.append(
            {
                "date": day,
                "key": key,
                "in_month": day.month == month_start.month,
                "is_today": day == today,
                "is_weekend": day.weekday() >= 5,
                "events": day_events,
                "event_count": len(day_events),
            }
        )

    weeks = [days[i : i + 7] for i in range(0, len(days), 7)]
    month_days = [day for day in days if day["in_month"]]
    active_days = [day for day in month_days if day["event_count"]]
    attended_events = [event for event in events if getattr(event, "attended", False)]
    free_events = [event for event in events if getattr(event, "is_free", False)]
    cities = sorted({event.location_city for event in events if event.location_city})
    sources = sorted({event.source for event in events if event.source})
    featured_days = sorted(active_days, key=lambda day: day["event_count"], reverse=True)[:3]
    upcoming_events = [event for event in events if event.start_time.date() >= today][:8]

    page_ctx = await ctx(
        request,
        active_page="calendar",
        month_start=month_start,
        month_label=month_start.strftime("%B %Y"),
        prev_month=prev_month,
        next_month=next_month_start,
        attended=attended,
        total_events=len(events),
        attended_events_count=len(attended_events),
        free_events_count=len(free_events),
        busy_days_count=len(active_days),
        source_count=len(sources),
        city_count=len(cities),
        weeks=weeks,
        featured_days=featured_days,
        upcoming_events=upcoming_events,
        cities=cities,
        sources=sources,
        today=today,
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
            value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
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
            [
                value
                for value in [event.location_name, event.location_address, event.location_city]
                if value
            ]
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
        {
            "request": request,
            "csrf_token": ensure_csrf_token(request),
            **job_template_context(job, target_id=target_id),
        },
    )


@app.post("/api/jobs/{job_id}/cancel", response_class=HTMLResponse)
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

    body = templates.get_template("partials/_job_status.html").render(
        request=request,
        csrf_token=ensure_csrf_token(request),
        **job_template_context(job, target_id=target_id),
    )
    if job.state == "running":
        return toast("Job is still running", "warning", body=body)
    return toast("Job cancelled", "success", body=body)


@app.get("/jobs", response_class=HTMLResponse)
async def jobs_page(
    request: Request,
    state: str = "",
    kind: str = "",
    source_id: str = "",
    q: str = "",
):
    user = await get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

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

    return templates.TemplateResponse(
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

    async def runner(_job) -> int:
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

    async def runner(job) -> int:
        async with Database(db_path) as job_db:
            await job.update(detail="Preparing tag batches…", result={"processed": 0, "total": 0, "succeeded": 0, "failed": 0})
            return await run_tag(job_db, progress_callback=lambda progress: job.update(detail=progress.get("summary", "Running…"), result=progress))

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

    async def runner(_job) -> int:
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

    async def runner(_job) -> str:
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
