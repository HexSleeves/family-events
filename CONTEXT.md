# Family Events - Project Context

## What This Is

Automated family event discovery for a parent of a 3-year-old in **Lafayette, Louisiana**.
Scrapes events from multiple sources, tags them for toddler-friendliness (via LLM or heuristics),
ranks by interests/weather/timing, and sends curated weekend notifications.

## Tech Stack

- **Python 3.12** with **uv** package manager
- **FastAPI** (web server + API)
- **SQLite** (WAL mode, via aiosqlite)
- **Jinja2** already in deps (currently unused - HTML is inline in Python)
- **httpx** + **BeautifulSoup** (scraping)
- **OpenAI API** (gpt-4o-mini for tagging, with heuristic fallback)
- **Pydantic v2** (all data models)
- **APScheduler** (cron: daily scrape 2AM, Friday notify 8AM)
- **ruff** (format + lint) and **ty** (type checking)

## Project Structure

```
family-events/
├── pyproject.toml          # deps, ruff config, ty config
├── .env / .env.example     # API keys (OpenAI, weather, Twilio, Telegram, Resend)
├── family_events.db        # SQLite database (auto-created)
├── family-events.service   # systemd: web server on port 8000
├── family-events-cron.service  # systemd: scheduler
├── src/
│   ├── config.py           # pydantic-settings, reads .env
│   ├── main.py             # CLI entry point (scrape/tag/notify/pipeline/serve/events)
│   ├── scheduler.py        # Pipeline orchestration: run_scrape, run_tag, run_notify
│   ├── cron.py             # APScheduler loop (daily_scrape_and_tag, friday_notification)
│   ├── db/
│   │   ├── models.py       # Event, EventTags, InterestProfile, Constraints
│   │   └── database.py     # Database class (async, upsert/query/update)
│   ├── scrapers/
│   │   ├── base.py         # BaseScraper ABC with _client() helper
│   │   ├── lafayette.py    # Moncus Park, Acadiana Arts, Science Museum (MEC plugin)
│   │   ├── brec.py         # BREC - Baton Rouge parks (HTML parsing)
│   │   ├── eventbrite.py   # Eventbrite (JSON-LD + HTML fallback, both cities)
│   │   ├── allevents.py    # AllEvents.in (both cities)
│   │   └── library.py      # Lafayette/BR libraries (LibCal - needs Playwright for JS)
│   ├── tagger/
│   │   └── llm.py          # EventTagger: OpenAI with heuristic fallback
│   ├── ranker/
│   │   ├── scoring.py      # score_event(), rank_events() - weighted multi-factor
│   │   └── weather.py      # WeatherService (OpenWeatherMap, with defaults)
│   ├── notifications/
│   │   ├── formatter.py    # format_console_message() - text notification
│   │   ├── dispatcher.py   # Routes to configured channels
│   │   ├── console.py      # Print to stdout
│   │   ├── sms.py          # Twilio
│   │   ├── telegram.py     # Telegram Bot API
│   │   └── email.py        # Resend
│   └── web/
│       └── app.py          # FastAPI app - ALL HTML IS INLINE HERE (358 lines)
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
Key methods: `upsert_event`, `get_events_for_weekend`, `get_untagged_events`,
`update_event_tags`, `get_recent_events`, `mark_attended`.

## API Endpoints (src/web/app.py)

### Pages (return HTML)
- `GET /` - Dashboard (stats, top events, action buttons)
- `GET /events` - All events table
- `GET /event/{id}` - Event detail with AI tags
- `GET /weekend` - Ranked weekend picks + notification preview

### API (return JSON)
- `POST /api/scrape` - Run all scrapers, returns `{count}`
- `POST /api/tag` - Tag untagged events, returns `{count}`
- `POST /api/notify` - Send weekend notification, returns `{message}`
- `POST /api/attend/{id}` - Mark event attended
- `GET /api/events` - Event list as JSON

## Current State

- ~1,500 events in DB (mostly BREC/Baton Rouge, ~93 Lafayette)
- All events tagged (heuristic - no OpenAI key configured yet)
- Web UI works but is all inline HTML in Python f-strings
- `ruff check` and `ty check` both pass clean
- Server runs on port 8000 via systemd

## Environment

- Running on exe.dev VM (noon-disk.exe.xyz)
- Port 8000 exposed via exe.dev HTTPS proxy
- systemd services: `family-events` (web) and `family-events-cron` (scheduler)
- `.env` has API keys (OPENAI_API_KEY, WEATHER_API_KEY, etc.)
