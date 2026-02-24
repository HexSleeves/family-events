"""FastAPI web admin for Family Events."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from src.db.database import Database
from src.db.models import Constraints, InterestProfile, Source, User
from src.notifications.formatter import format_console_message
from src.ranker.scoring import rank_events
from src.ranker.weather import WeatherService
from src.scrapers.analyzer import PageAnalyzer
from src.scrapers.recipe import ScrapeRecipe
from src.scrapers.router import extract_domain, is_builtin_domain
from src.web.auth import (
    get_current_user,
    hash_password,
    login_session,
    logout_session,
    verify_password,
)

db = Database()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


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


def _change_theme(theme: str):
    """Return an HTMLResponse that triggers a theme change via HX-Trigger."""
    payload = json.dumps({"changeTheme": {"theme": theme}})
    return HTMLResponse(
        content="",
        status_code=200,
        headers={"HX-Trigger": payload},
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    yield
    await db.close()


app = FastAPI(title="Family Events", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", "dev-secret-change-me-in-prod"),
    session_cookie="session",
    max_age=60 * 60 * 24 * 30,  # 30 days
)


async def _ctx(request: Request, **extra: object) -> dict:
    """Build base template context with current user."""
    user = await get_current_user(request, db)
    return {"request": request, "current_user": user, **extra}


# ----- Auth Pages -----


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = await get_current_user(request, db)
    if user:
        return RedirectResponse("/profile", status_code=302)
    return templates.TemplateResponse("login.html", await _ctx(request))


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request):
    form = await request.form()
    email = str(form.get("email", "")).strip().lower()
    password = str(form.get("password", ""))

    user = await db.get_user_by_email(email)
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "login.html",
            {**await _ctx(request), "error": "Invalid email or password."},
        )

    login_session(request, user)
    return RedirectResponse("/profile", status_code=302)


@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    user = await get_current_user(request, db)
    if user:
        return RedirectResponse("/profile", status_code=302)
    return templates.TemplateResponse("signup.html", await _ctx(request))


@app.post("/signup", response_class=HTMLResponse)
async def signup_submit(request: Request):
    form = await request.form()
    email = str(form.get("email", "")).strip().lower()
    display_name = str(form.get("display_name", "")).strip()
    password = str(form.get("password", ""))
    confirm = str(form.get("confirm_password", ""))

    errors: list[str] = []
    if not email or "@" not in email:
        errors.append("Valid email is required.")
    if not display_name:
        errors.append("Display name is required.")
    if len(password) < 6:
        errors.append("Password must be at least 6 characters.")
    if password != confirm:
        errors.append("Passwords don't match.")
    if not errors and await db.get_user_by_email(email):
        errors.append("An account with this email already exists.")

    if errors:
        return templates.TemplateResponse(
            "signup.html",
            {
                **await _ctx(request),
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
    return RedirectResponse("/profile", status_code=302)


@app.get("/logout")
async def logout(request: Request):
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
        {**await _ctx(request), "sources": sources},
    )


@app.post("/api/profile/location", response_class=HTMLResponse)
async def api_update_location(request: Request):
    user = await get_current_user(request, db)
    if not user:
        return HTMLResponse("Unauthorized", status_code=401)
    form = await request.form()
    home_city = str(form.get("home_city", "Lafayette")).strip()
    pref_raw = str(form.get("preferred_cities", "")).strip()
    preferred = [c.strip() for c in pref_raw.split(",") if c.strip()]
    if not preferred:
        preferred = [home_city]
    await db.update_user(user.id, home_city=home_city, preferred_cities=preferred)
    return _toast("Location updated")


@app.post("/api/profile/preferences", response_class=HTMLResponse)
async def api_update_preferences(request: Request):
    user = await get_current_user(request, db)
    if not user:
        return HTMLResponse("Unauthorized", status_code=401)
    form = await request.form()
    loves = [x.strip() for x in str(form.get("loves", "")).split(",") if x.strip()]
    likes = [x.strip() for x in str(form.get("likes", "")).split(",") if x.strip()]
    dislikes = [x.strip() for x in str(form.get("dislikes", "")).split(",") if x.strip()]
    nap_time = str(form.get("nap_time", "13:00-15:00")).strip()
    bedtime = str(form.get("bedtime", "19:30")).strip()
    budget = float(form.get("budget", 30.0))
    max_drive = int(form.get("max_drive", 45))

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
    user = await get_current_user(request, db)
    if not user:
        return HTMLResponse("Unauthorized", status_code=401)
    form = await request.form()
    theme = str(form.get("theme", "auto")).strip()
    if theme not in ("light", "dark", "auto"):
        theme = "auto"
    await db.update_user(user.id, theme=theme)
    return _change_theme(theme)


@app.post("/api/profile/notifications", response_class=HTMLResponse)
async def api_update_notifications(request: Request):
    user = await get_current_user(request, db)
    if not user:
        return HTMLResponse("Unauthorized", status_code=401)
    form = await request.form()
    channels = form.getlist("channels")
    if not channels:
        channels = ["console"]
    email_to = str(form.get("email_to", "")).strip()
    child_name = str(form.get("child_name", "")).strip() or "Your Little One"
    await db.update_user(
        user.id,
        notification_channels=[str(c) for c in channels],
        email_to=email_to,
        child_name=child_name,
    )
    return _toast("Notification settings updated")


@app.post("/api/profile/password", response_class=HTMLResponse)
async def api_update_password(request: Request):
    user = await get_current_user(request, db)
    if not user:
        return HTMLResponse("Unauthorized", status_code=401)
    form = await request.form()
    current = str(form.get("current_password", ""))
    new_pw = str(form.get("new_password", ""))
    confirm = str(form.get("confirm_password", ""))
    if not verify_password(current, user.password_hash):
        return _toast("Current password is incorrect", "error")
    if len(new_pw) < 6:
        return _toast("New password must be at least 6 characters", "error")
    if new_pw != confirm:
        return _toast("Passwords don't match", "error")
    await db.update_user(user.id, password_hash=hash_password(new_pw))
    return _toast("Password changed")


# ----- Pages -----


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    events = await db.get_recent_events(days=30)
    total = len(events)
    tagged = sum(1 for e in events if e.tags)
    untagged = total - tagged
    sources = len(set(e.source for e in events))

    top_events = sorted(
        [e for e in events if e.tags], key=lambda e: e.tags.toddler_score, reverse=True
    )[:5]

    return templates.TemplateResponse(
        "dashboard.html",
        await _ctx(
            request,
            total=total,
            tagged=tagged,
            untagged=untagged,
            sources=sources,
            top_events=top_events,
        ),
    )


@app.get("/events", response_class=HTMLResponse)
async def events_page(
    request: Request,
    q: str = "",
    city: str = "",
    source: str = "",
    tagged: str = "",
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
        score_min=score_min_int,
        sort=sort,
        page=page,
        per_page=per_page,
    )
    total_pages = max(1, (total + per_page - 1) // per_page)
    filters = await db.get_filter_options()

    ctx = await _ctx(
        request,
        events=events,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        q=q,
        city=city,
        source=source,
        tagged=tagged,
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

    return templates.TemplateResponse(
        "event_detail.html",
        await _ctx(request, event=event, raw_data=raw_data),
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

    return templates.TemplateResponse(
        "weekend.html",
        await _ctx(
            request,
            saturday=saturday,
            sunday=sunday,
            weather=weather,
            ranked=ranked,
            message=message,
        ),
    )


# ----- Sources Pages -----


@app.get("/sources", response_class=HTMLResponse)
async def sources_page(request: Request):
    sources = await db.get_all_sources()
    builtin_stats = await db.get_filter_options()
    return templates.TemplateResponse(
        "sources.html",
        await _ctx(request, sources=sources, builtin_stats=builtin_stats),
    )


@app.get("/source/{source_id}", response_class=HTMLResponse)
async def source_detail(request: Request, source_id: str):
    source = await db.get_source(source_id)
    if not source:
        return HTMLResponse("Source not found", status_code=404)
    events_from_source, _ = await db.search_events(
        days=90, source=f"custom:{source_id}", per_page=10
    )
    recipe = None
    if source.recipe_json:
        recipe = ScrapeRecipe.model_validate_json(source.recipe_json)
    return templates.TemplateResponse(
        "source_detail.html",
        await _ctx(request, source=source, recipe=recipe, events=events_from_source),
    )


# ----- API Endpoints (return HTML snippets for HTMX) -----


@app.post("/api/scrape", response_class=HTMLResponse)
async def api_scrape():
    from src.scheduler import run_scrape

    count = await run_scrape(db)
    return _toast(f"Scraped {count} events")


@app.post("/api/tag", response_class=HTMLResponse)
async def api_tag():
    from src.scheduler import run_tag

    count = await run_tag(db)
    return _toast(f"Tagged {count} events")


@app.post("/api/notify", response_class=HTMLResponse)
async def api_notify(request: Request):
    from src.scheduler import run_notify

    user = await get_current_user(request, db)
    await run_notify(db, user=user)
    return _toast("Notification sent!")


@app.post("/api/attend/{event_id}", response_class=HTMLResponse)
async def api_attend(event_id: str):
    await db.mark_attended(event_id)
    return HTMLResponse(
        '<span class="inline-block px-4 py-2 rounded-lg bg-gray-200 text-gray-600 font-semibold text-sm">'
        "Attended \u2705</span>"
    )


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
    form = await request.form()
    url = str(form.get("url", "")).strip()
    name = str(form.get("name", "")).strip()
    if not url:
        return _toast("Please enter a URL", "error")

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
    user = await get_current_user(request, db)
    source = Source(
        name=name,
        url=url,
        domain=domain,
        status="analyzing",
        user_id=user.id if user else None,
    )
    await db.create_source(source)

    # Analyze in-line (for now; could be background task later)
    try:
        analyzer = PageAnalyzer()
        recipe = await analyzer.analyze(url)
        await db.update_source_recipe(
            source.id,
            recipe.model_dump_json(),
            status="active" if recipe.confidence >= 0.3 else "failed",
        )
        return _toast(
            f"Source added! Strategy: {recipe.strategy}, confidence: {recipe.confidence:.0%}",
            body="<script>setTimeout(()=>location.reload(),1000)</script>",
        )
    except Exception as e:
        await db.update_source_status(source.id, status="failed", error=str(e))
        return _toast(f"Analysis failed: {e}", "error")


@app.post("/api/sources/{source_id}/analyze", response_class=HTMLResponse)
async def api_reanalyze(source_id: str):
    source = await db.get_source(source_id)
    if not source:
        return HTMLResponse("Not found", status_code=404)
    await db.update_source_status(source_id, status="analyzing")
    try:
        analyzer = PageAnalyzer()
        recipe = await analyzer.analyze(source.url)
        await db.update_source_recipe(
            source_id,
            recipe.model_dump_json(),
            status="active" if recipe.confidence >= 0.3 else "failed",
        )
        return _toast(
            f"Re-analyzed! Confidence: {recipe.confidence:.0%}",
            body="<script>setTimeout(()=>location.reload(),1000)</script>",
        )
    except Exception as e:
        await db.update_source_status(source_id, status="failed", error=str(e))
        return _toast(f"Analysis failed: {e}", "error")


@app.post("/api/sources/{source_id}/test", response_class=HTMLResponse)
async def api_test_source(request: Request, source_id: str):
    source = await db.get_source(source_id)
    if not source or not source.recipe_json:
        return HTMLResponse("No recipe to test", status_code=400)
    try:
        from src.scrapers.generic import GenericScraper

        recipe = ScrapeRecipe.model_validate_json(source.recipe_json)
        scraper = GenericScraper(url=source.url, source_id=source.id, recipe=recipe)
        events = await scraper.scrape()
        return templates.TemplateResponse(
            "partials/_source_test_results.html",
            {"request": request, "events": events, "count": len(events)},
        )
    except Exception as e:
        return _toast(f"Test failed: {e}", "error")


@app.post("/api/sources/{source_id}/toggle", response_class=HTMLResponse)
async def api_toggle_source(source_id: str):
    enabled = await db.toggle_source(source_id)
    state = "enabled" if enabled else "disabled"
    return _toast(
        f"Source {state}",
        body="<script>setTimeout(()=>location.reload(),500)</script>",
    )


@app.delete("/api/sources/{source_id}", response_class=HTMLResponse)
async def api_delete_source(source_id: str):
    await db.delete_source(source_id)
    return _toast(
        "Source deleted",
        body='<script>setTimeout(()=>location.href="/sources",500)</script>',
    )
