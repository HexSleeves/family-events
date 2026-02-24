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
from src.db.models import InterestProfile
from src.notifications.formatter import format_console_message
from src.ranker.scoring import rank_events
from src.ranker.weather import WeatherService

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
async def events_page(request: Request):
    events = await db.get_recent_events(days=30)
    events.sort(key=lambda e: e.start_time)
    return templates.TemplateResponse("events.html", {"request": request, "events": events})


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
