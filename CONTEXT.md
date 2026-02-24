# Family Events - Project Context

## What This Is

Automated family event discovery for a parent of a 3-year-old in **Lafayette, Louisiana**.
Scrapes events from multiple sources, tags them for toddler-friendliness (via LLM or heuristics),
ranks by interests/weather/timing, and sends curated weekend notifications.

## Tech Stack

- **Python 3.12** with **uv** package manager
- **FastAPI** (web server + API)
- **SQLite** (WAL mode, via aiosqlite)
- **Jinja2** templates with `{% extends %}` / `{% include %}` inheritance
- **HTMX 2.0.4** for all interactive updates (no custom JS)
- **Tailwind CSS** via CDN play script (no build step)
- **httpx** + **BeautifulSoup** (scraping)
- **OpenAI API** (gpt-4o-mini for tagging, with heuristic fallback)
- **Pydantic v2** (all data models)
- **APScheduler** (cron: daily scrape 2AM, Friday notify 8AM)
- **ruff** (format + lint) and **ty** (type checking)

## Project Structure

```
family-events/
├── pyproject.toml              # deps, ruff config, ty config
├── .env / .env.example         # API keys (OpenAI, weather, Twilio, Telegram, Resend)
├── family_events.db            # SQLite database (auto-created)
├── family-events.service       # systemd: web server on port 8000
├── family-events-cron.service  # systemd: scheduler
├── CONTEXT.md                  # This file - project context for AI agents
├── TODO.md                     # Current tasks and next steps
├── src/
│   ├── config.py               # pydantic-settings, reads .env
│   ├── main.py                 # CLI entry point (scrape/tag/notify/pipeline/serve/events)
│   ├── scheduler.py            # Pipeline orchestration: run_scrape, run_tag, run_notify
│   ├── cron.py                 # APScheduler loop (daily_scrape_and_tag, friday_notification)
│   ├── db/
│   │   ├── models.py           # Event, EventTags, InterestProfile, Constraints
│   │   └── database.py         # Database class (async, upsert/query/search/update)
│   ├── scrapers/
│   │   ├── base.py             # BaseScraper ABC with _client() helper
│   │   ├── lafayette.py        # Moncus Park, Acadiana Arts, Science Museum (MEC plugin)
│   │   ├── brec.py             # BREC - Baton Rouge parks (HTML parsing)
│   │   ├── eventbrite.py       # Eventbrite (JSON-LD + HTML fallback, both cities)
│   │   ├── allevents.py        # AllEvents.in (both cities)
│   │   └── library.py          # Lafayette/BR libraries (LibCal - needs Playwright for JS)
│   ├── tagger/
│   │   └── llm.py              # EventTagger: OpenAI with heuristic fallback
│   ├── ranker/
│   │   ├── scoring.py          # score_event(), rank_events() - weighted multi-factor
│   │   └── weather.py          # WeatherService (OpenWeatherMap, with defaults)
│   ├── notifications/
│   │   ├── formatter.py        # format_console_message() - text notification
│   │   ├── dispatcher.py       # Routes to configured channels
│   │   ├── console.py          # Print to stdout
│   │   ├── sms.py              # Twilio
│   │   ├── telegram.py         # Telegram Bot API
│   │   └── email.py            # Resend
│   └── web/
│       ├── app.py              # FastAPI routes only (221 lines, no HTML)
│       └── templates/
│           ├── base.html            # Shared layout, Tailwind CDN, HTMX CDN, skeleton CSS
│           ├── dashboard.html       # Stats cards, action buttons, top events
│           ├── events.html          # Search bar, filters, paginated table
│           ├── event_detail.html    # Event info, AI tags grid, raw data
│           ├── weekend.html         # Ranked picks, weather, notification preview
│           └── partials/
│               ├── _event_card.html       # Reusable event card (dashboard + weekend)
│               ├── _event_row.html        # Table row (events page)
│               ├── _events_table.html     # Table + pagination (HTMX swap target)
│               ├── _tags_grid.html        # AI tags 2-column grid
│               ├── _stats.html            # Stats bar (dashboard)
│               ├── _notification.html     # Notification preview
│               ├── _skeleton_table.html   # 8-row shimmer table skeleton
│               └── _skeleton_action.html  # Spinner + bar for action status
```

## Key Data Models (src/db/models.py)

### Event
```
id, source, source_url, source_id (dedup key),
title, description, location_name, location_address, location_city,
latitude, longitude, start_time, end_time, is_recurring, recurrence_rule,
is_free, price_min, price_max, image_url, scraped_at, raw_data (JSON),
tags (EventTags | None), attended (bool)
```

### EventTags (LLM-generated)
```
toddler_score (0-10), age_min/max_recommended,
indoor_outdoor, noise_level, crowd_level, energy_level,
stroller_friendly, parking_available, bathroom_accessible,
food_available, nap_compatible, weather_dependent,
good_for_rain, good_for_heat,
categories (list[str]), confidence_score,
parent_attention_required, meltdown_risk
```

### InterestProfile
```
loves: [animals, playground, water_play, music, trains, art_messy]
likes: [nature_walks, story_time, dancing]
dislikes: [loud_crowds, sitting_still_long, dark_spaces]
constraints: home_city=Lafayette, nap 1-3pm, bedtime 7:30pm, $30 budget, 45min drive
```

## Scoring Algorithm (src/ranker/scoring.py)

```
score = toddler_score * 3.0
      + interest_match * 2.5
      + weather_compat * 2.0
      + city_proximity * 2.0   (Lafayette +10, BR +2, other -5)
      + timing_score * 1.5     (morning bonus, nap/bedtime penalty)
      + logistics * 1.0        (stroller, parking, bathrooms, meltdown risk)
      + novelty * 0.5
```

## Database (src/db/database.py)

Single `events` table with `UNIQUE(source, source_id)` for dedup.
Key methods:
- `upsert_event` - insert or update event by (source, source_id)
- `search_events` - paginated search with filters (q, city, source, tagged, score_min, sort)
- `get_events_for_weekend` - events for a given Saturday/Sunday
- `get_untagged_events` - events with no AI tags
- `update_event_tags` - set tags JSON for an event
- `get_recent_events` - events within next N days
- `get_filter_options` - distinct cities and sources for dropdowns
- `mark_attended` - mark event as attended

## Web UI (src/web/)

### Frontend Architecture
- **Jinja2** templates with `{% extends "base.html" %}` inheritance
- **HTMX** for all interactivity (no custom JavaScript)
  - Search: `hx-get` with `keyup changed delay:300ms` debounce
  - Filters: `hx-trigger="change"` via `.auto-submit` class
  - Pagination: `hx-get` with `hx-push-url` for bookmarkable URLs
  - Action buttons: `hx-post` with `hx-indicator` and `hx-disabled-elt`
  - Attend: `hx-post` with `hx-swap="outerHTML"` for in-place swap
- **Tailwind CSS** via CDN play script (no build step)
- **Loading skeletons**:
  - Global: 3px indeterminate progress bar at top of viewport
  - Events table: full skeleton overlay with 8 shimmer rows
  - Buttons: CSS spinner + disabled state during requests
  - Action status: spinner + skeleton bar

### Pages (return HTML)
- `GET /` - Dashboard (stats, action buttons with spinners, top 5 events)
- `GET /events` - Paginated events table with search, filters, sort (25/page)
- `GET /event/{id}` - Event detail with AI tags grid, raw data, attend button
- `GET /weekend` - Ranked weekend picks with weather, notification preview

### API Endpoints (return HTML snippets for HTMX)
- `POST /api/scrape` - Run scrapers, returns success HTML snippet
- `POST /api/tag` - Tag events, returns success HTML snippet
- `POST /api/notify` - Send notification, returns success HTML snippet
- `POST /api/attend/{id}` - Mark attended, returns "Attended ✅" label
- `GET /api/events` - JSON event list (used by CLI)

### HTMX Partial Rendering
The `/events` route detects `HX-Request` header and returns only the
`_events_table.html` partial (table + pagination) instead of the full page.
This enables instant search/filter/pagination without full page reloads.

## Current State

- ~1,496 events in DB (1,361 BREC, 55 Lafayette, 48 Eventbrite, 31 AllEvents)
- 2 cities: Baton Rouge (~1,400), Lafayette (~95)
- 1,494 tagged (heuristic), 2 untagged
- Web UI fully templated with Jinja2 + HTMX + Tailwind
- Events page: paginated (25/page), searchable, filterable (city, source, tagged, score, sort)
- Loading skeletons on all HTMX interactions
- `ruff check` and `ty check` both pass clean
- Server runs on port 8000 via systemd

## Environment

- Running on exe.dev VM (noon-disk.exe.xyz)
- Port 8000 exposed via exe.dev HTTPS proxy: https://noon-disk.exe.xyz:8000/
- systemd services: `family-events` (web) and `family-events-cron` (scheduler)
- `.env` has API keys (OPENAI_API_KEY, WEATHER_API_KEY, etc.)
