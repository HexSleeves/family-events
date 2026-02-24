"""FastAPI web admin for Family Events."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.db.database import Database
from src.db.models import InterestProfile, Source
from src.notifications.formatter import format_console_message
from src.ranker.scoring import rank_events
from src.ranker.weather import WeatherService
from src.scrapers.analyzer import PageAnalyzer
from src.scrapers.recipe import ScrapeRecipe
from src.scrapers.router import extract_domain, is_builtin_domain

db = Database()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    yield
    await db.close()


app = FastAPI(title="Family Events", lifespan=lifespan)


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
        {
            "request": request,
            "total": total,
            "tagged": tagged,
            "untagged": untagged,
            "sources": sources,
            "top_events": top_events,
        },
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

    ctx = {
        "request": request,
        "events": events,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "q": q,
        "city": city,
        "source": source,
        "tagged": tagged,
        "score_min": score_min_int,
        "sort": sort,
        "cities": filters["cities"],
        "sources": filters["sources"],
    }

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
        {"request": request, "event": event, "raw_data": raw_data},
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
    profile = InterestProfile()
    ranked = rank_events(tagged, profile, weather)
    message = format_console_message(ranked, weather)

    return templates.TemplateResponse(
        "weekend.html",
        {
            "request": request,
            "saturday": saturday,
            "sunday": sunday,
            "weather": weather,
            "ranked": ranked,
            "message": message,
        },
    )


# ----- Sources Pages -----


@app.get("/sources", response_class=HTMLResponse)
async def sources_page(request: Request):
    sources = await db.get_all_sources()
    builtin_stats = await db.get_filter_options()
    return templates.TemplateResponse(
        "sources.html",
        {"request": request, "sources": sources, "builtin_stats": builtin_stats},
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
        {"request": request, "source": source, "recipe": recipe, "events": events_from_source},
    )


# ----- API Endpoints (return HTML snippets for HTMX) -----


@app.post("/api/scrape", response_class=HTMLResponse)
async def api_scrape():
    from src.scheduler import run_scrape

    count = await run_scrape(db)
    return HTMLResponse(
        f'<div class="text-green-600 font-semibold">\u2705 Scraped {count} events</div>'
    )


@app.post("/api/tag", response_class=HTMLResponse)
async def api_tag():
    from src.scheduler import run_tag

    count = await run_tag(db)
    return HTMLResponse(
        f'<div class="text-green-600 font-semibold">\u2705 Tagged {count} events</div>'
    )


@app.post("/api/notify", response_class=HTMLResponse)
async def api_notify():
    from src.scheduler import run_notify

    await run_notify(db)
    return HTMLResponse('<div class="text-green-600 font-semibold">\u2705 Notification sent!</div>')


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
        return HTMLResponse(
            '<div class="text-red-600 font-semibold">\u274c Please enter a URL</div>'
        )

    # Check for built-in domain
    if is_builtin_domain(url):
        return HTMLResponse(
            '<div class="text-blue-600 font-semibold">'
            "\u2705 We already have built-in support for this site!</div>"
        )

    # Check for duplicate
    existing = await db.get_source_by_url(url)
    if existing:
        return HTMLResponse(
            '<div class="text-orange-600 font-semibold">'
            "\u26a0\ufe0f This URL has already been added</div>"
        )

    # Create source
    domain = extract_domain(url)
    if not name:
        name = domain.replace(".", " ").title()
    source = Source(name=name, url=url, domain=domain, status="analyzing")
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
        return HTMLResponse(
            f'<div class="text-green-600 font-semibold">'
            f"\u2705 Source added! Strategy: {recipe.strategy}, "
            f"confidence: {recipe.confidence:.0%}</div>"
            f"<script>setTimeout(()=>location.reload(),1000)</script>"
        )
    except Exception as e:
        await db.update_source_status(source.id, status="failed", error=str(e))
        return HTMLResponse(
            f'<div class="text-red-600 font-semibold">\u274c Analysis failed: {e}</div>'
        )


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
        return HTMLResponse(
            f'<div class="text-green-600 font-semibold">'
            f"\u2705 Re-analyzed! Confidence: {recipe.confidence:.0%}</div>"
            f"<script>setTimeout(()=>location.reload(),1000)</script>"
        )
    except Exception as e:
        await db.update_source_status(source_id, status="failed", error=str(e))
        return HTMLResponse(
            f'<div class="text-red-600 font-semibold">\u274c Analysis failed: {e}</div>'
        )


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
        return HTMLResponse(
            f'<div class="text-red-600 font-semibold">\u274c Test failed: {e}</div>'
        )


@app.post("/api/sources/{source_id}/toggle", response_class=HTMLResponse)
async def api_toggle_source(source_id: str):
    enabled = await db.toggle_source(source_id)
    state = "enabled" if enabled else "disabled"
    icon = "\u2705" if enabled else "\u23f8\ufe0f"
    return HTMLResponse(
        f'<div class="text-green-600 font-semibold">{icon} Source {state}</div>'
        f"<script>setTimeout(()=>location.reload(),500)</script>"
    )


@app.delete("/api/sources/{source_id}", response_class=HTMLResponse)
async def api_delete_source(source_id: str):
    await db.delete_source(source_id)
    return HTMLResponse(
        '<div class="text-green-600 font-semibold">\u2705 Source deleted</div>'
        '<script>setTimeout(()=>location.href="/sources",500)</script>'
    )
