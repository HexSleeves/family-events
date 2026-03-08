"""FastAPI web admin for Family Events."""

from __future__ import annotations

import json
import logging
import secrets
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote_plus
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.datastructures import FormData
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from src.config import settings
from src.db.database import Database
from src.db.models import Constraints, InterestProfile, Job, Source, User
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
from src.scrapers.analyzer import PageAnalyzer, validate_public_http_url
from src.scrapers.recipe import ScrapeRecipe
from src.scrapers.router import extract_domain, is_builtin_domain
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
from src.web.jobs import job_registry

db = Database()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

logger = logging.getLogger("uvicorn.error")

_rate_limit_store: dict[str, deque[float]] = {}
_bulk_unattend_undo_store: dict[str, list[str]] = {}


def _toast(
    message: str,
    variant: str = "success",
    *,
    status_code: int = 200,
    body: str = "",
) -> HTMLResponse:
    """Return an HTMLResponse that triggers a toast notification via HX-Trigger."""
    payload = json.dumps({"showToast": {"message": message, "variant": variant}})
    return HTMLResponse(
        content=body,
        status_code=status_code,
        headers={"HX-Trigger": payload},
    )


def _change_theme(theme: str) -> HTMLResponse:
    """Return an HTMLResponse that triggers a theme change + toast via HX-Trigger."""
    label = {"light": "Light", "dark": "Dark", "auto": "System"}.get(theme, theme)
    payload = json.dumps(
        {
            "changeTheme": {"theme": theme},
            "showToast": {"message": f"Theme set to {label}", "variant": "success"},
        }
    )
    return HTMLResponse(
        content="",
        status_code=200,
        headers={"HX-Trigger": payload},
    )


def _null_response() -> HTMLResponse:
    """Return an HTMLResponse that does nothing."""
    return HTMLResponse(
        content="",
        status_code=204,
        headers={"HX-Trigger": json.dumps({})},
    )


def _fmt_job_time(value: datetime | None) -> str:
    """Format job timestamps for UI."""
    return value.astimezone(UTC).strftime("%b %d, %I:%M:%S %p UTC") if value else "—"


def _job_result_value(job: Job) -> Any:
    """Parse persisted JSON job result when present."""
    if not job.result_json:
        return None
    try:
        return json.loads(job.result_json)
    except json.JSONDecodeError:
        return job.result_json


def _job_result_summary(job: Job) -> str | None:
    """Return a concise success summary for structured job results."""
    result = _job_result_value(job)
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


def _job_status_message(job: Job) -> str:
    """Return human-readable job status text."""
    if job.state == "running":
        return f"{job.label} is running…"
    if job.state == "failed":
        return f"{job.label} failed: {job.error or 'Unknown error'}"
    summary = _job_result_summary(job)
    return f"{job.label} completed: {summary}" if summary else f"{job.label} completed"


def _require_login(user: User | None) -> HTMLResponse | None:
    """Require an authenticated user for API routes."""
    if user:
        return None
    return _toast("Please log in first", "error", status_code=401)


def _client_ip(request: Request) -> str:
    """Return best-effort client IP, honoring one trusted forwarded header value."""
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    if forwarded:
        return forwarded
    return request.client.host if request.client else "unknown"


def _rate_limit_key(request: Request, route: str) -> str:
    """Build per-IP, per-route rate limit key."""
    return f"{_client_ip(request)}:{route}"


def _check_rate_limit(
    request: Request,
    route: str,
    *,
    limit: int | None = None,
    window: int | None = None,
    message: str = "Too many requests. Try again in a moment.",
) -> HTMLResponse | None:
    """Simple sliding-window in-memory rate limiter."""
    limit = max(1, limit or settings.rate_limit_max_requests)
    window = max(1, window or settings.rate_limit_window_seconds)

    now = time.monotonic()
    key = _rate_limit_key(request, route)
    hits = _rate_limit_store.setdefault(key, deque())

    cutoff = now - window
    while hits and hits[0] < cutoff:
        hits.popleft()

    if len(hits) >= limit:
        return _toast(message, "warning", status_code=429)

    hits.append(now)
    return None


def _format_ts(ts: datetime | None) -> str | None:
    """Format UTC timestamps for health payload."""
    if not ts:
        return None
    return ts.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _expected_origin(request: Request) -> str:
    """Return the expected origin for same-origin checks."""
    base_url = settings.app_base_url.strip().rstrip("/") or str(request.base_url).rstrip("/")
    return base_url


def _require_safe_origin(request: Request) -> HTMLResponse | None:
    """Basic origin/referer check for unsafe methods."""
    expected = _expected_origin(request)
    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if not value:
            continue
        if value.startswith(expected):
            return None
        return _toast("Request blocked by origin policy", "error", status_code=403)
    return None


async def _require_csrf(request: Request) -> tuple[FormData | None, HTMLResponse | None]:
    """Return parsed form plus any CSRF denial response."""
    form = await request.form()
    if denied := _require_safe_origin(request):
        return form, denied
    expected = request.session.get("csrf_token")
    provided = (
        request.headers.get("X-CSRF-Token", "").strip() or str(form.get("csrf_token", "")).strip()
    )
    if not expected or not provided or not secrets.compare_digest(str(expected), provided):
        return (
            form,
            _toast(
                "Security check failed. Refresh the page and try again.",
                "error",
                status_code=403,
            ),
        )
    return form, None


async def _require_login_and_csrf(
    request: Request,
) -> tuple[User | None, FormData | None, HTMLResponse | None]:
    """Return current user, parsed form, plus any auth/csrf denial response."""
    user = await get_current_user(request, db)
    if denied := _require_login(user):
        return user, None, denied
    form, denied = await _require_csrf(request)
    return user, form, denied


def _validate_source_url(url: str) -> str | None:
    """Return error message if source URL is unsafe or invalid."""
    if len(url) > 2048:
        return "URL is too long"
    try:
        validate_public_http_url(url)
    except ValueError as exc:
        return str(exc)
    return None


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log each request with status and duration."""

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000
        ip = _client_ip(request)
        logger.info(
            "%s %s status=%s duration_ms=%.1f ip=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            ip,
        )
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    yield
    await db.close()


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


async def _ctx(request: Request, **extra: object) -> dict:
    """Build base template context with current user."""
    user = await get_current_user(request, db)
    csrf_token = ensure_csrf_token(request)
    return {
        "request": request,
        "current_user": user,
        "csrf_token": csrf_token,
        "active_page": extra.pop("active_page", ""),
        **extra,
    }


def _job_template_context(
    job: Job,
    *,
    target_id: str,
    refresh_path: str = "",
    refresh_select: str = "",
    refresh_target_id: str = "",
    auto_refresh_history: bool = False,
) -> dict[str, Any]:
    """Build a shared template context for rendering a job card."""
    return {
        "job": job,
        "target_id": target_id,
        "message": _job_status_message(job),
        "started_at": _fmt_job_time(job.started_at or job.created_at),
        "finished_at": _fmt_job_time(job.finished_at),
        "result": _job_result_value(job),
        "result_summary": _job_result_summary(job),
        "refresh_path": refresh_path,
        "refresh_select": refresh_select,
        "refresh_target_id": refresh_target_id,
        "auto_refresh_history": auto_refresh_history,
    }


def _render_job_cards(
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
        _job_template_context(
            job,
            target_id=f"{target_prefix}{job.id}",
            refresh_path=refresh_path,
            refresh_select=refresh_select,
            refresh_target_id=refresh_target_id,
            auto_refresh_history=auto_refresh_history,
        )
        for job in jobs
    ]


async def _start_background_job(
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
    """Start or reuse a background job and return an HTMX polling shell."""
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

    body = templates.get_template("partials/_job_status.html").render(
        request=request,
        **_job_template_context(job, target_id=target_id),
    )
    return _toast(message, variant, body=body)


# ----- Auth Pages -----


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = await get_current_user(request, db)
    if user:
        return RedirectResponse("/profile", status_code=302)
    return templates.TemplateResponse("login.html", await _ctx(request, active_page="auth"))


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request):
    form, denied = await _require_csrf(request)
    if denied:
        return denied
    if throttled := _check_rate_limit(
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
            {**await _ctx(request, active_page="auth"), "error": "Invalid email or password."},
        )

    login_session(request, user)
    rotate_csrf_token(request)
    return RedirectResponse("/profile", status_code=302)


@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    user = await get_current_user(request, db)
    if user:
        return RedirectResponse("/profile", status_code=302)
    return templates.TemplateResponse("signup.html", await _ctx(request, active_page="auth"))


@app.post("/signup", response_class=HTMLResponse)
async def signup_submit(request: Request):
    form, denied = await _require_csrf(request)
    if denied:
        return denied
    if throttled := _check_rate_limit(
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
                **await _ctx(request, active_page="auth"),
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
    _user, _form, denied = await _require_login_and_csrf(request)
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
        {**await _ctx(request, active_page="profile"), "sources": sources},
    )


@app.post("/api/profile/location", response_class=HTMLResponse)
async def api_update_location(request: Request):
    user, form, denied = await _require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None and form is not None
    home_city = str(form.get("home_city", "Lafayette")).strip()
    pref_raw = str(form.get("preferred_cities", "")).strip()
    preferred = [c.strip() for c in pref_raw.split(",") if c.strip()]
    if not preferred:
        preferred = [home_city]
    await db.update_user(user.id, home_city=home_city, preferred_cities=preferred)
    return _toast("Location updated")


@app.post("/api/profile/preferences", response_class=HTMLResponse)
async def api_update_preferences(request: Request):
    user, form, denied = await _require_login_and_csrf(request)
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
    return _toast("Preferences updated")


@app.post("/api/profile/theme", response_class=HTMLResponse)
async def api_update_theme(request: Request):
    user, form, denied = await _require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None and form is not None
    user_theme = user.theme
    theme = str(form.get("theme", user_theme)).strip()
    if theme == user_theme:
        return _null_response()
    if theme not in ("light", "dark", "auto"):
        theme = "auto"
    await db.update_user(user.id, theme=theme)
    return _change_theme(theme)


@app.post("/api/profile/notifications", response_class=HTMLResponse)
async def api_update_notifications(request: Request):
    user, form, denied = await _require_login_and_csrf(request)
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
        return _toast("Add a notification email to enable email delivery", "error")
    if "sms" in channels and not sms_to:
        return _toast("Add a phone number to enable SMS delivery", "error")
    await db.update_user(
        user.id,
        notification_channels=[str(c) for c in channels],
        email_to=email_to,
        sms_to=sms_to,
        child_name=child_name,
    )
    return _toast("Notification settings updated")


@app.post("/api/profile/password", response_class=HTMLResponse)
async def api_update_password(request: Request):
    user, form, denied = await _require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None and form is not None
    current = str(form.get("current_password", ""))
    new_pw = str(form.get("new_password", ""))
    confirm = str(form.get("confirm_password", ""))
    if not verify_password(current, user.password_hash):
        return _toast("Current password is incorrect", "error")
    password_errors = validate_password(new_pw)
    if password_errors:
        return _toast(password_errors[0], "error")
    if new_pw != confirm:
        return _toast("Passwords don't match", "error")
    await db.update_user(user.id, password_hash=hash_password(new_pw))
    return _toast("Password changed")


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
                "latest_scraped_at": _format_ts(latest_scrape_at),
            }
        },
    }
    return JSONResponse(payload, status_code=200 if db_ok else 503)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    events = await db.get_recent_events(days=30)
    total = len(events)
    tagged = sum(1 for e in events if e.tags)
    untagged = total - tagged
    sources = len(set(e.source for e in events))
    timestamps = await db.get_pipeline_timestamps()
    user = await get_current_user(request, db)
    recent_jobs = await db.list_jobs(owner_user_id=user.id, limit=8) if user else []
    recent_job_cards = _render_job_cards(
        recent_jobs,
        target_prefix="job-history-",
        refresh_path="/",
        refresh_select="#section-jobs",
        refresh_target_id="section-jobs",
    )

    top_events = sorted(
        [e for e in events if e.tags], key=lambda e: e.tags.toddler_score, reverse=True
    )[:5]

    # Also grab category sections for discover page
    arts_events = sorted(
        [e for e in events if e.tags and "arts" in (e.tags.categories or [])],
        key=lambda e: e.tags.toddler_score,
        reverse=True,
    )[:8]
    outdoor_events = sorted(
        [e for e in events if e.tags and e.tags.indoor_outdoor in ("outdoor", "both")],
        key=lambda e: e.tags.toddler_score,
        reverse=True,
    )[:8]
    nature_events = sorted(
        [e for e in events if e.tags and "nature" in (e.tags.categories or [])],
        key=lambda e: e.tags.toddler_score,
        reverse=True,
    )[:8]

    near_city = user.home_city if user else "Lafayette"
    near_you_events = sorted(
        [e for e in events if e.tags and e.location_city == near_city],
        key=lambda e: e.tags.toddler_score,
        reverse=True,
    )[:8]

    return templates.TemplateResponse(
        "dashboard.html",
        await _ctx(
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

    ctx = await _ctx(
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

    # HTMX partial: only return the table + pagination fragment
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("partials/_events_table.html", ctx)

    return templates.TemplateResponse("events.html", ctx)


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
        [v for v in [event.location_name, event.location_address, event.location_city] if v]
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
            e
            for e in candidates
            if e.id != event.id
            and e.tags
            and e.location_city == event.location_city
            and abs((e.start_time - event.start_time).days) <= 14
        ]
        related.sort(key=lambda e: e.tags.toddler_score if e.tags else 0, reverse=True)
        related_events = [(e, float(e.tags.toddler_score if e.tags else 0)) for e in related[:4]]

    return templates.TemplateResponse(
        "event_detail.html",
        await _ctx(
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

    first_weekday = month_start.weekday()  # Monday=0
    grid_start = month_start - timedelta(days=first_weekday)
    days: list[dict[str, Any]] = []
    for i in range(42):
        day = grid_start + timedelta(days=i)
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

    ctx = await _ctx(
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
        return templates.TemplateResponse("partials/_calendar_grid.html", ctx)

    return templates.TemplateResponse("calendar.html", ctx)


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
            [v for v in [event.location_name, event.location_address, event.location_city] if v]
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

    weather_svc = WeatherService()
    weather = await weather_svc.get_weekend_forecast(saturday, sunday)

    events = await db.get_events_for_weekend(saturday.isoformat(), sunday.isoformat())
    if not events:
        events = await db.get_recent_events(days=14)

    tagged = [e for e in events if e.tags]
    user = await get_current_user(request, db)
    profile = user.interest_profile if user else InterestProfile()
    child_name = user.child_name if user else "Your Little One"
    ranked = rank_events(tagged, profile, weather)
    message = format_console_message(ranked, weather, child_name)

    weather_summary, weather_tone, weather_tips = summarize_weekend_recommendation(weather)

    return templates.TemplateResponse(
        "weekend.html",
        await _ctx(
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


# ----- Sources Pages -----


@app.get("/sources", response_class=HTMLResponse)
async def sources_page(request: Request):
    user = await get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    sources = await db.get_user_sources(user.id)
    builtin_stats = await db.get_filter_options()
    recent_jobs = await db.list_jobs(owner_user_id=user.id, limit=10)
    recent_job_cards = _render_job_cards(
        recent_jobs,
        target_prefix="job-history-",
        refresh_path="/sources",
        refresh_select="#sources-jobs-panel",
        refresh_target_id="sources-jobs-panel",
    )
    return templates.TemplateResponse(
        "sources.html",
        await _ctx(
            request,
            active_page="sources",
            sources=sources,
            builtin_stats=builtin_stats,
            recent_jobs=recent_jobs,
            recent_job_cards=recent_job_cards,
        ),
    )


@app.get("/source/{source_id}", response_class=HTMLResponse)
async def source_detail(request: Request, source_id: str):
    user = await get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    source = await db.get_source(source_id)
    if not source:
        return HTMLResponse("Source not found", status_code=404)
    if source.user_id and source.user_id != user.id:
        return HTMLResponse("Forbidden", status_code=403)

    events_from_source, _ = await db.search_events(
        days=90, source=f"custom:{source_id}", per_page=10
    )
    recipe = None
    if source.recipe_json:
        recipe = ScrapeRecipe.model_validate_json(source.recipe_json)
    recent_jobs = await db.list_jobs(owner_user_id=user.id, source_id=source.id, limit=10)
    recent_job_cards = _render_job_cards(
        recent_jobs,
        target_prefix="job-history-",
        refresh_path=f"/source/{source.id}",
        refresh_select="#source-job-history-panel",
        refresh_target_id="source-job-history-panel",
    )
    return templates.TemplateResponse(
        "source_detail.html",
        await _ctx(
            request,
            active_page="sources",
            source=source,
            recipe=recipe,
            events=events_from_source,
            recent_jobs=recent_jobs,
            recent_job_cards=recent_job_cards,
        ),
    )


# ----- API Endpoints (return HTML snippets for HTMX) -----


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
        {"request": request, **_job_template_context(job, target_id=target_id)},
    )


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
    job_cards = _render_job_cards(
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
        await _ctx(
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
    user, _form, denied = await _require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := _check_rate_limit(request, "api_scrape"):
        return throttled

    from src.scheduler import run_scrape

    return await _start_background_job(
        request,
        user=user,
        kind="scrape",
        key="pipeline:scrape",
        label="Scrape job",
        runner=run_scrape,
        target_id="dashboard-job-status",
    )


@app.post("/api/tag", response_class=HTMLResponse)
async def api_tag(request: Request):
    user, _form, denied = await _require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := _check_rate_limit(request, "api_tag"):
        return throttled

    from src.scheduler import run_tag

    return await _start_background_job(
        request,
        user=user,
        kind="tag",
        key="pipeline:tag",
        label="Tag job",
        runner=run_tag,
        target_id="dashboard-job-status",
    )


@app.post("/api/dedupe", response_class=HTMLResponse)
async def api_dedupe(request: Request):
    user, _form, denied = await _require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := _check_rate_limit(request, "api_dedupe"):
        return throttled

    async def _runner() -> int:
        async with Database() as job_db:
            result = await job_db.dedupe_existing_events()
            return int(result["merged"])

    return await _start_background_job(
        request,
        user=user,
        kind="dedupe",
        key="pipeline:dedupe",
        label="Dedupe job",
        runner=_runner,
        target_id="dashboard-job-status",
    )


@app.post("/api/notify", response_class=HTMLResponse)
async def api_notify(request: Request):
    user, _form, denied = await _require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := _check_rate_limit(request, "api_notify"):
        return throttled

    from src.scheduler import run_notify

    return await _start_background_job(
        request,
        user=user,
        kind="notify",
        key=f"pipeline:notify:{user.id}",
        label="Notification job",
        runner=lambda: run_notify(user=user),
        target_id="dashboard-job-status",
    )


@app.post("/api/attend/{event_id}", response_class=HTMLResponse)
async def api_attend(request: Request, event_id: str):
    user, _form, denied = await _require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := _check_rate_limit(request, "api_attend"):
        return throttled

    await db.mark_attended(event_id)
    return _toast("Marked attended")


@app.post("/api/unattend/{event_id}", response_class=HTMLResponse)
async def api_unattend(request: Request, event_id: str):
    user, _form, denied = await _require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := _check_rate_limit(request, "api_unattend"):
        return throttled

    await db.db.execute("UPDATE events SET attended = 0 WHERE id = :id", {"id": event_id})
    await db.db.commit()
    return _toast("Marked as not attended")


@app.post("/api/unattend-bulk", response_class=HTMLResponse)
async def api_unattend_bulk(request: Request):
    user, _form, denied = await _require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := _check_rate_limit(request, "api_unattend_bulk"):
        return throttled

    event_ids = [
        str(eid) for eid in (_form.getlist("event_ids") if _form else []) if str(eid).strip()
    ]
    if not event_ids:
        return _toast("Select at least one event", "warning")

    await db.db.executemany(
        "UPDATE events SET attended = 0 WHERE id = ?",
        [(eid,) for eid in event_ids],
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
    user, _form, denied = await _require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := _check_rate_limit(request, "api_unattend_bulk_undo"):
        return throttled

    event_ids = _bulk_unattend_undo_store.pop(undo_token, [])
    if not event_ids:
        return _toast("Nothing to undo", "warning")

    await db.db.executemany(
        "UPDATE events SET attended = 1 WHERE id = ?",
        [(eid,) for eid in event_ids],
    )
    await db.db.commit()
    return _toast(f"Restored {len(event_ids)} event(s)")


@app.get("/api/events")
async def api_events():
    events = await db.get_recent_events(days=30)
    return [
        {
            "id": e.id,
            "title": e.title,
            "source": e.source,
            "city": e.location_city,
            "start_time": e.start_time.isoformat(),
            "tagged": e.tags is not None,
            "toddler_score": e.tags.toddler_score if e.tags else None,
        }
        for e in events
    ]


# ----- Source API Endpoints -----


@app.post("/api/sources", response_class=HTMLResponse)
async def api_add_source(request: Request):
    user, _form, denied = await _require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := _check_rate_limit(request, "api_add_source"):
        return throttled

    form = _form
    url = str(form.get("url", "")).strip() if form else ""
    name = str(form.get("name", "")).strip() if form else ""
    if not url:
        return _toast("Please enter a URL", "error")
    if url_error := _validate_source_url(url):
        return _toast(url_error, "error")

    # Check for built-in domain
    if is_builtin_domain(url):
        return _toast("We already have built-in support for this site!", "info")

    # Check for duplicate
    existing = await db.get_source_by_url(url)
    if existing:
        return _toast("This URL has already been added", "warning")

    # Create source
    domain = extract_domain(url)
    if not name:
        name = domain.replace(".", " ").title()
    source = Source(
        name=name,
        url=url,
        domain=domain,
        status="analyzing",
        user_id=user.id,
    )
    await db.create_source(source)

    async def _runner() -> dict[str, Any]:
        async with Database() as job_db:
            try:
                analyzer = PageAnalyzer()
                recipe = await analyzer.analyze(url)
                await job_db.update_source_recipe(
                    source.id,
                    recipe.model_dump_json(),
                    status="active" if recipe.confidence >= 0.3 else "failed",
                )
                return {
                    "summary": f"{recipe.strategy} strategy at {recipe.confidence:.0%} confidence",
                    "strategy": recipe.strategy,
                    "confidence": recipe.confidence,
                    "notes": recipe.notes,
                    "recipe": recipe.model_dump(mode="json"),
                }
            except Exception as exc:
                await job_db.update_source_status(source.id, status="failed", error=str(exc))
                raise

    return await _start_background_job(
        request,
        user=user,
        kind="source-analyze",
        key=f"source:analyze:{source.id}",
        label=f"Analyzing {source.name}",
        runner=_runner,
        target_id=f"source-job-{source.id}",
        source_id=source.id,
    )


@app.post("/api/sources/{source_id}/analyze", response_class=HTMLResponse)
async def api_reanalyze(request: Request, source_id: str):
    user, _form, denied = await _require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := _check_rate_limit(request, "api_reanalyze_source"):
        return throttled

    source = await db.get_source(source_id)
    if not source:
        return HTMLResponse("Not found", status_code=404)
    if source.user_id and source.user_id != user.id:
        return HTMLResponse("Forbidden", status_code=403)
    await db.update_source_status(source_id, status="analyzing")

    async def _runner() -> dict[str, Any]:
        async with Database() as job_db:
            source_for_job = await job_db.get_source(source_id)
            if not source_for_job:
                raise ValueError("Source not found")
            try:
                analyzer = PageAnalyzer()
                recipe = await analyzer.analyze(source_for_job.url)
                await job_db.update_source_recipe(
                    source_id,
                    recipe.model_dump_json(),
                    status="active" if recipe.confidence >= 0.3 else "failed",
                )
                return {
                    "summary": f"{recipe.strategy} strategy at {recipe.confidence:.0%} confidence",
                    "strategy": recipe.strategy,
                    "confidence": recipe.confidence,
                    "notes": recipe.notes,
                    "recipe": recipe.model_dump(mode="json"),
                }
            except Exception as exc:
                await job_db.update_source_status(source_id, status="failed", error=str(exc))
                raise

    return await _start_background_job(
        request,
        user=user,
        kind="source-analyze",
        key=f"source:analyze:{source_id}",
        label=f"Analyzing {source.name}",
        runner=_runner,
        target_id=f"source-job-{source_id}",
        source_id=source_id,
    )


@app.post("/api/sources/{source_id}/test", response_class=HTMLResponse)
async def api_test_source(request: Request, source_id: str):
    user, _form, denied = await _require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := _check_rate_limit(request, "api_test_source"):
        return throttled

    source = await db.get_source(source_id)
    if not source or not source.recipe_json:
        return HTMLResponse("No recipe to test", status_code=400)
    if source.user_id and source.user_id != user.id:
        return HTMLResponse("Forbidden", status_code=403)

    async def _runner() -> dict[str, Any]:
        from src.scrapers.generic import GenericScraper

        async with Database() as job_db:
            source_for_job = await job_db.get_source(source_id)
            if not source_for_job or not source_for_job.recipe_json:
                raise ValueError("No recipe to test")
            recipe = ScrapeRecipe.model_validate_json(source_for_job.recipe_json)
            scraper = GenericScraper(
                url=source_for_job.url,
                source_id=source_for_job.id,
                recipe=recipe,
            )
            events = await scraper.scrape()
            sample_events = [
                {
                    "title": event.title,
                    "start_time": event.start_time.isoformat(),
                    "location_name": event.location_name,
                    "location_city": event.location_city,
                    "source_url": event.source_url,
                }
                for event in events[:5]
            ]
            return {
                "summary": f"{len(events)} events found",
                "count": len(events),
                "sample_events": sample_events,
                "source_url": source_for_job.url,
                "strategy": recipe.strategy,
            }

    return await _start_background_job(
        request,
        user=user,
        kind="source-test",
        key=f"source:test:{source_id}",
        label=f"Testing {source.name}",
        runner=_runner,
        target_id=f"source-job-{source_id}",
        source_id=source_id,
    )


@app.post("/api/sources/{source_id}/toggle", response_class=HTMLResponse)
async def api_toggle_source(request: Request, source_id: str):
    user, _form, denied = await _require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := _check_rate_limit(request, "api_toggle_source"):
        return throttled

    source = await db.get_source(source_id)
    if not source:
        return HTMLResponse("Not found", status_code=404)
    if source.user_id and source.user_id != user.id:
        return HTMLResponse("Forbidden", status_code=403)

    enabled = await db.toggle_source(source_id)
    state = "enabled" if enabled else "disabled"
    return _toast(
        f"Source {state}",
        body="<script>setTimeout(()=>location.reload(),500)</script>",
    )


@app.delete("/api/sources/{source_id}", response_class=HTMLResponse)
async def api_delete_source(request: Request, source_id: str):
    user, _form, denied = await _require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := _check_rate_limit(request, "api_delete_source"):
        return throttled

    source = await db.get_source(source_id)
    if not source:
        return HTMLResponse("Not found", status_code=404)
    if source.user_id and source.user_id != user.id:
        return HTMLResponse("Forbidden", status_code=403)

    await db.delete_source(source_id)
    return _toast(
        "Source deleted",
        body='<script>setTimeout(()=>location.href="/sources",500)</script>',
    )


@app.exception_handler(404)
async def not_found_handler(request: Request, _exc: Exception) -> HTMLResponse:
    """Render friendly 404 page for missing routes."""
    return templates.TemplateResponse(
        "404.html",
        await _ctx(request),
        status_code=404,
    )


@app.exception_handler(500)
async def server_error_handler(request: Request, _exc: Exception) -> HTMLResponse:
    """Render friendly 500 page for unhandled server errors."""
    return templates.TemplateResponse(
        "500.html",
        await _ctx(request),
        status_code=500,
    )
