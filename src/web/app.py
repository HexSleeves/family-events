"""FastAPI web admin for Family Events."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, timedelta

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from src.db.database import Database
from src.db.models import InterestProfile
from src.notifications.formatter import format_console_message
from src.ranker.scoring import rank_events
from src.ranker.weather import WeatherService

db = Database()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    yield
    await db.close()


app = FastAPI(title="Family Events", lifespan=lifespan)


# ----- HTML Templates (inline for simplicity) -----


def _page(title: str, content: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{title} - Family Events</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; background: #f8f9fa; color: #1a1a2e; line-height: 1.6; }}
        .container {{ max-width: 960px; margin: 0 auto; padding: 1rem; }}
        header {{ background: linear-gradient(135deg, #6366f1, #8b5cf6); color: white; padding: 1.5rem 0; margin-bottom: 1.5rem; }}
        header h1 {{ font-size: 1.5rem; }}
        header .container {{ display: flex; align-items: center; gap: 1rem; }}
        nav {{ display: flex; gap: 0.5rem; margin-left: auto; }}
        nav a {{ color: white; text-decoration: none; padding: 0.4rem 0.8rem; border-radius: 6px; background: rgba(255,255,255,0.15); font-size: 0.9rem; }}
        nav a:hover {{ background: rgba(255,255,255,0.3); }}
        .card {{ background: white; border-radius: 12px; padding: 1.2rem; margin-bottom: 1rem; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
        .card h3 {{ margin-bottom: 0.4rem; }}
        .badge {{ display: inline-block; padding: 0.15rem 0.5rem; border-radius: 99px; font-size: 0.75rem; font-weight: 600; }}
        .badge-green {{ background: #dcfce7; color: #166534; }}
        .badge-blue {{ background: #dbeafe; color: #1e40af; }}
        .badge-orange {{ background: #ffedd5; color: #9a3412; }}
        .badge-gray {{ background: #f3f4f6; color: #374151; }}
        .meta {{ color: #6b7280; font-size: 0.85rem; }}
        .score {{ font-size: 1.8rem; font-weight: 700; color: #6366f1; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 1rem; }}
        .stats {{ display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.5rem; }}
        .stat {{ background: white; border-radius: 12px; padding: 1rem 1.5rem; box-shadow: 0 1px 3px rgba(0,0,0,0.08); text-align: center; flex: 1; min-width: 120px; }}
        .stat-value {{ font-size: 2rem; font-weight: 700; color: #6366f1; }}
        .stat-label {{ font-size: 0.8rem; color: #6b7280; text-transform: uppercase; letter-spacing: 0.05em; }}
        .btn {{ display: inline-block; padding: 0.5rem 1rem; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 0.85rem; cursor: pointer; border: none; }}
        .btn-primary {{ background: #6366f1; color: white; }}
        .btn-primary:hover {{ background: #4f46e5; }}
        .btn-sm {{ padding: 0.3rem 0.6rem; font-size: 0.75rem; }}
        table {{ width: 100%; border-collapse: collapse; }}
        th, td {{ text-align: left; padding: 0.6rem; border-bottom: 1px solid #e5e7eb; font-size: 0.85rem; }}
        th {{ font-weight: 600; color: #6b7280; font-size: 0.75rem; text-transform: uppercase; }}
        pre {{ background: #1e1e2e; color: #cdd6f4; padding: 1rem; border-radius: 8px; overflow-x: auto; font-size: 0.8rem; white-space: pre-wrap; }}
        .actions {{ display: flex; gap: 0.5rem; margin-bottom: 1.5rem; }}
    </style>
</head>
<body>
    <header>
        <div class="container">
            <h1>🌟 Family Events</h1>
            <nav>
                <a href="/">🏠 Dashboard</a>
                <a href="/events">📅 Events</a>
                <a href="/weekend">🎉 Weekend</a>
            </nav>
        </div>
    </header>
    <div class="container">
        {content}
    </div>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    events = await db.get_recent_events(days=30)
    total = len(events)
    tagged = sum(1 for e in events if e.tags)
    untagged = total - tagged
    sources = len(set(e.source for e in events))

    # Top events by toddler score
    top_events = sorted(
        [e for e in events if e.tags], key=lambda e: e.tags.toddler_score, reverse=True
    )[:5]

    top_html = ""
    for e in top_events:
        cats = ", ".join(e.tags.categories) if e.tags else ""
        top_html += f"""
        <div class="card">
            <div style="display:flex;align-items:center;gap:1rem">
                <div class="score">{e.tags.toddler_score}</div>
                <div>
                    <h3>{e.title}</h3>
                    <div class="meta">📍 {e.location_city} · 📅 {e.start_time.strftime("%b %d, %I:%M%p")} · {e.source}</div>
                    <div style="margin-top:0.3rem">
                        <span class="badge badge-blue">{e.tags.indoor_outdoor}</span>
                        <span class="badge badge-green">{e.tags.meltdown_risk} meltdown</span>
                        <span class="badge badge-gray">{cats}</span>
                    </div>
                </div>
            </div>
        </div>"""

    content = f"""
    <div class="stats">
        <div class="stat"><div class="stat-value">{total}</div><div class="stat-label">Events</div></div>
        <div class="stat"><div class="stat-value">{tagged}</div><div class="stat-label">Tagged</div></div>
        <div class="stat"><div class="stat-value">{untagged}</div><div class="stat-label">Untagged</div></div>
        <div class="stat"><div class="stat-value">{sources}</div><div class="stat-label">Sources</div></div>
    </div>

    <div class="actions">
        <button class="btn btn-primary" onclick="fetch('/api/scrape',{{method:'POST'}}).then(r=>r.json()).then(d=>{{alert('Scraped '+d.count+' events');location.reload()}})">🔄 Run Scrapers</button>
        <button class="btn btn-primary" onclick="fetch('/api/tag',{{method:'POST'}}).then(r=>r.json()).then(d=>{{alert('Tagged '+d.count+' events');location.reload()}})">🏷️ Tag Events</button>
        <button class="btn btn-primary" onclick="fetch('/api/notify',{{method:'POST'}}).then(r=>r.json()).then(d=>{{alert('Sent!');location.reload()}})">📬 Send Notification</button>
    </div>

    <h2 style="margin-bottom:1rem">⭐ Top Toddler-Friendly Events</h2>
    {top_html if top_html else '<p class="meta">No tagged events yet. Run scrapers then tag!</p>'}
    """
    return HTMLResponse(_page("Dashboard", content))


@app.get("/events", response_class=HTMLResponse)
async def events_page():
    events = await db.get_recent_events(days=30)
    events.sort(key=lambda e: e.start_time)

    rows = ""
    for e in events:
        ts = e.tags.toddler_score if e.tags else "-"
        cats = ", ".join(e.tags.categories[:3]) if e.tags else ""
        tag_badge = (
            f'<span class="badge badge-green">{ts}/10</span>'
            if e.tags
            else '<span class="badge badge-orange">untagged</span>'
        )
        rows += f"""
        <tr>
            <td>{e.start_time.strftime("%m/%d %a")}</td>
            <td>{e.start_time.strftime("%-I:%M%p")}</td>
            <td><a href="{e.source_url}" target="_blank">{e.title[:60]}</a></td>
            <td>{e.location_city}</td>
            <td><span class="badge badge-gray">{e.source}</span></td>
            <td>{tag_badge}</td>
            <td>{cats}</td>
            <td>
                <a href="/event/{e.id}" class="btn btn-sm btn-primary">View</a>
            </td>
        </tr>"""

    content = f"""
    <h2 style="margin-bottom:1rem">📅 All Events ({len(events)})</h2>
    <div class="card" style="overflow-x:auto">
        <table>
            <thead><tr>
                <th>Date</th><th>Time</th><th>Event</th><th>City</th><th>Source</th><th>Score</th><th>Categories</th><th></th>
            </tr></thead>
            <tbody>{rows}</tbody>
        </table>
    </div>"""
    return HTMLResponse(_page("Events", content))


@app.get("/event/{event_id}", response_class=HTMLResponse)
async def event_detail(event_id: str):
    import json

    async with db.db.execute("SELECT * FROM events WHERE id = :id", {"id": event_id}) as cursor:
        row = await cursor.fetchone()
    if not row:
        return HTMLResponse(_page("Not Found", "<p>Event not found.</p>"), status_code=404)

    from src.db.database import _row_to_event

    event = _row_to_event(row)

    tags_html = ""
    if event.tags:
        t = event.tags
        tags_html = f"""
        <div class="card">
            <h3>🏷️ AI Tags</h3>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.5rem;margin-top:0.5rem;font-size:0.9rem">
                <div><strong>Toddler Score:</strong> <span class="score" style="font-size:1.2rem">{t.toddler_score}/10</span></div>
                <div><strong>Age Range:</strong> {t.age_min_recommended}-{t.age_max_recommended}</div>
                <div><strong>Indoor/Outdoor:</strong> {t.indoor_outdoor}</div>
                <div><strong>Noise Level:</strong> {t.noise_level}</div>
                <div><strong>Crowd:</strong> {t.crowd_level}</div>
                <div><strong>Energy:</strong> {t.energy_level}</div>
                <div><strong>Stroller-Friendly:</strong> {"✅" if t.stroller_friendly else "❌"}</div>
                <div><strong>Parking:</strong> {"✅" if t.parking_available else "❌"}</div>
                <div><strong>Bathrooms:</strong> {"✅" if t.bathroom_accessible else "❌"}</div>
                <div><strong>Food:</strong> {"✅" if t.food_available else "❌"}</div>
                <div><strong>Nap-Compatible:</strong> {"✅" if t.nap_compatible else "❌"}</div>
                <div><strong>Weather-Dependent:</strong> {"✅" if t.weather_dependent else "❌"}</div>
                <div><strong>Good for Rain:</strong> {"✅" if t.good_for_rain else "❌"}</div>
                <div><strong>Good for Heat:</strong> {"✅" if t.good_for_heat else "❌"}</div>
                <div><strong>Meltdown Risk:</strong> <span class="badge {"badge-green" if t.meltdown_risk == "low" else "badge-orange"}">{t.meltdown_risk}</span></div>
                <div><strong>Parent Attention:</strong> {t.parent_attention_required}</div>
                <div><strong>Categories:</strong> {", ".join(t.categories)}</div>
                <div><strong>Confidence:</strong> {t.confidence_score:.0%}</div>
            </div>
        </div>"""

    content = f"""
    <div class="card">
        <h2>{event.title}</h2>
        <div class="meta" style="margin:0.5rem 0">
            📍 {event.location_name}, {event.location_city} ·
            📅 {event.start_time.strftime("%A, %B %d at %-I:%M %p")}
            {f" - {event.end_time.strftime('%-I:%M %p')}" if event.end_time else ""} ·
            💵 {"Free" if event.is_free else f"${event.price_min or '?'}"} ·
            <span class="badge badge-gray">{event.source}</span>
        </div>
        <p style="margin-top:0.8rem">{event.description[:1000] if event.description else "No description available."}</p>
        <div style="margin-top:1rem">
            <a href="{event.source_url}" target="_blank" class="btn btn-primary">View Original</a>
            <button class="btn btn-primary" style="background:#059669" onclick="fetch('/api/attend/{event.id}',{{method:'POST'}}).then(()=>alert('Marked as attended!'))">✅ Mark Attended</button>
        </div>
    </div>
    {tags_html}
    <div class="card">
        <h3>Raw Data</h3>
        <pre>{json.dumps(event.raw_data, indent=2, default=str)[:3000]}</pre>
    </div>"""
    return HTMLResponse(_page(event.title, content))


@app.get("/weekend", response_class=HTMLResponse)
async def weekend_page():
    today = date.today()
    days_until_sat = (5 - today.weekday()) % 7
    if days_until_sat == 0:
        days_until_sat = 0  # It's Saturday, show this weekend
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

    ranked_html = ""
    for i, (event, score) in enumerate(ranked[:10]):
        medal = ["🥇", "🥈", "🥉"][i] if i < 3 else f"#{i + 1}"
        cats = ", ".join(event.tags.categories) if event.tags else ""
        ranked_html += f"""
        <div class="card" style="{"border-left: 4px solid #6366f1;" if i < 3 else ""}">
            <div style="display:flex;align-items:center;gap:1rem">
                <div style="font-size:1.5rem;min-width:2rem">{medal}</div>
                <div style="flex:1">
                    <h3><a href="/event/{event.id}">{event.title}</a></h3>
                    <div class="meta">
                        📍 {event.location_city} · 🕐 {event.start_time.strftime("%a %-I:%M%p")} ·
                        💵 {"Free" if event.is_free else f"${event.price_min or '?'}"}
                    </div>
                    <div style="margin-top:0.3rem">{cats}</div>
                </div>
                <div style="text-align:right">
                    <div class="score">{score:.0f}</div>
                    <div class="meta">points</div>
                </div>
            </div>
        </div>"""

    content = f"""
    <h2 style="margin-bottom:0.5rem">🎉 Weekend Plan: {saturday.strftime("%b %d")} - {sunday.strftime("%b %d")}</h2>
    <p class="meta" style="margin-bottom:1rem">
        {weather["saturday"].icon} Sat {weather["saturday"].temp_high_f:.0f}°F ·
        {weather["sunday"].icon} Sun {weather["sunday"].temp_high_f:.0f}°F
    </p>
    {ranked_html if ranked_html else "<p>No ranked events. Run scrapers and tagger first!</p>"}

    <div class="card" style="margin-top:2rem">
        <h3>📨 Notification Preview</h3>
        <pre>{message}</pre>
    </div>"""
    return HTMLResponse(_page("Weekend Plans", content))


# ----- API Endpoints -----


@app.post("/api/scrape")
async def api_scrape():
    from src.scheduler import run_scrape

    count = await run_scrape(db)
    return {"count": count}


@app.post("/api/tag")
async def api_tag():
    from src.scheduler import run_tag

    count = await run_tag(db)
    return {"count": count}


@app.post("/api/notify")
async def api_notify():
    from src.scheduler import run_notify

    message = await run_notify(db)
    return {"message": message}


@app.post("/api/attend/{event_id}")
async def api_attend(event_id: str):
    await db.mark_attended(event_id)
    return {"ok": True}


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
