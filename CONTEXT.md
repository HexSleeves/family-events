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
- **HTMX 2.0.4** for all interactive updates (no custom JS framework)
- **Tailwind CSS** via CDN play script (no build step)
- **httpx** + **BeautifulSoup** (scraping)
- **OpenAI API** (gpt-4o-mini for tagging, with heuristic fallback)
- **Pydantic v2** (all data models)
- **APScheduler** (cron: daily scrape 2AM, Friday notify 8AM)
- **bcrypt** (password hashing) + **itsdangerous** (session cookies)
- **ruff** (format + lint) and **ty** (type checking)

## Project Structure

```bash
family-events/
├── pyproject.toml              # deps, ruff config, ty config
├── .env / .env.example         # API keys only (secrets + infra)
├── family_events.db            # SQLite database (auto-created)
├── family-events.service       # systemd: web server on port 8000
├── family-events-cron.service  # systemd: scheduler
├── CONTEXT.md                  # This file
├── TODO.md                     # Current tasks and next steps
├── src/
│   ├── config.py               # pydantic-settings, reads .env (extra="ignore")
│   ├── main.py                 # CLI entry point (scrape/tag/notify/pipeline/serve/events)
│   ├── scheduler.py            # Pipeline orchestration: run_scrape, run_tag, run_notify
│   ├── cron.py                 # APScheduler loop (daily_scrape_and_tag, friday_notification)
│   ├── db/
│   │   ├── models.py           # Event, EventTags, InterestProfile, User, Source, Constraints
│   │   └── database.py         # Database class (async, all CRUD + migrations)
│   ├── scrapers/
│   │   ├── base.py             # BaseScraper ABC with _client() helper
│   │   ├── lafayette.py        # Moncus Park, Acadiana Arts, Science Museum (MEC plugin)
│   │   ├── brec.py             # BREC - Baton Rouge parks (HTML parsing)
│   │   ├── eventbrite.py       # Eventbrite (JSON-LD + HTML fallback, both cities)
│   │   ├── allevents.py        # AllEvents.in (both cities)
│   │   ├── library.py          # Lafayette/BR libraries (LibCal - needs Playwright)
│   │   ├── generic.py          # Generic CSS/JSON-LD replay scraper for user sources
│   │   ├── analyzer.py         # LLM page analyzer - generates ScrapeRecipe from URL
│   │   ├── recipe.py           # ScrapeRecipe, CssSelectors models
│   │   └── router.py           # Domain router: built-in vs generic scraper dispatch
│   ├── tagger/
│   │   └── llm.py              # EventTagger: OpenAI with heuristic fallback
│   ├── ranker/
│   │   ├── scoring.py          # score_event(), rank_events() - weighted multi-factor
│   │   └── weather.py          # WeatherService (OpenWeatherMap, with defaults)
│   ├── notifications/
│   │   ├── formatter.py        # format_console_message() - text notification
│   │   ├── dispatcher.py       # Routes to configured channels (per-user)
│   │   ├── console.py          # Print to stdout
│   │   ├── sms.py              # Twilio
│   │   ├── telegram.py         # Telegram Bot API
│   │   └── email.py            # Resend (accepts per-user to_email)
│   └── web/
│       ├── app.py              # FastAPI routes + _toast() + _ctx() helpers (594 lines)
│       ├── auth.py             # hash_password, verify_password, session helpers
│       └── templates/          # 20 Jinja2 templates
│           ├── base.html            # Layout, nav (auth-aware), dark mode, toast system
│           ├── dashboard.html       # Stats cards, action buttons, top events
│           ├── events.html          # Search, filters, paginated table
│           ├── event_detail.html    # Event info, AI tags grid, raw data
│           ├── weekend.html         # Ranked picks, weather, notification preview
│           ├── sources.html         # Source list, add-source form
│           ├── source_detail.html   # Source info, recipe, test scrape
│           ├── login.html           # Login form
│           ├── signup.html          # Signup form
│           ├── profile.html         # Profile sections (theme, location, prefs, etc.)
│           └── partials/
│               ├── _event_card.html       # Reusable event card
│               ├── _event_row.html        # Table row (events page)
│               ├── _events_table.html     # Table + pagination (HTMX swap target)
│               ├── _tags_grid.html        # AI tags 2-column grid
│               ├── _stats.html            # Stats bar (dashboard)
│               ├── _notification.html     # Notification preview
│               ├── _source_card.html      # Source card for sources page
│               ├── _source_test_results.html  # Test scrape results
│               ├── _skeleton_table.html   # 8-row shimmer table skeleton
│               └── _skeleton_action.html  # Spinner + bar for action status
```

## Key Data Models (src/db/models.py)

### User

```
id, email, display_name, password_hash,
home_city, preferred_cities, theme (light/dark/auto),
notification_channels, email_to, child_name,
interest_profile (InterestProfile), created_at, updated_at
```

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

### Source

```
id, name, url, domain, user_id (nullable FK to users),
builtin, recipe_json (ScrapeRecipe JSON), enabled, status,
last_scraped_at, last_event_count, last_error, created_at, updated_at
```

### InterestProfile

```
loves: [animals, playground, water_play, music, trains, art_messy]
likes: [nature_walks, story_time, dancing]
dislikes: [loud_crowds, sitting_still_long, dark_spaces]
constraints: home_city, nap_time, bedtime, budget_per_event, max_drive_time_minutes
```

## Database Tables

- **events** — `UNIQUE(source, source_id)` for dedup
- **sources** — user-added scraping sources with recipes
- **users** — accounts with preferences, theme, notification settings

## Authentication

- **Session-based** via Starlette `SessionMiddleware` (signed cookies, 30-day expiry)
- **bcrypt** password hashing
- `get_current_user()` resolves session → User on every request
- `_ctx()` helper injects `current_user` into all template contexts
- Nav bar shows login/signup or user name/logout based on auth state

## Toast Notification System

All API success/error messages use toast notifications instead of inline HTML:

- **Server:** `_toast(message, variant)` returns empty `HTMLResponse` with `HX-Trigger` header
- **Client:** JS in `base.html` listens for `htmx:afterRequest`, parses `HX-Trigger` header, creates styled toast element
- **4 variants:** success (green), error (red), warning (amber), info (blue)
- Toasts slide in from top-right, auto-dismiss after 3.5s, clickable to dismiss early
- Uses inline styles (not Tailwind classes) for dynamic elements since Tailwind CDN can't JIT-compile dynamically created classes
- Forms that only need toast feedback use `hx-swap="none"`

## Dark Mode

- Tailwind `darkMode: 'class'` — toggled by `class="dark"` on `<html>`
- User preference stored in `users.theme` (light/dark/auto)
- Auto mode respects `prefers-color-scheme` media query
- All templates use `dark:` variant classes for backgrounds, text, borders

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

## Web UI Routes

### Pages (return full HTML)

| Route | Auth | Description |
|-------|------|-------------|
| `GET /` | No | Dashboard — stats, action buttons, top 5 events |
| `GET /events` | No | Paginated events table with search/filters (25/page) |
| `GET /event/{id}` | No | Event detail with AI tags, raw data, attend button |
| `GET /weekend` | No | Ranked weekend picks with weather |
| `GET /sources` | No | Source list + add-source form |
| `GET /source/{id}` | No | Source detail with recipe + test scrape |
| `GET /login` | No | Login form |
| `GET /signup` | No | Signup form |
| `GET /profile` | Yes | Profile page (theme, location, prefs, sources, password) |
| `GET /logout` | Yes | Clear session, redirect to `/` |

### API Endpoints (return toast or HTML snippets for HTMX)

| Route | Returns | Description |
|-------|---------|-------------|
| `POST /api/scrape` | Toast | Run all scrapers |
| `POST /api/tag` | Toast | Tag untagged events |
| `POST /api/notify` | Toast | Send notification |
| `POST /api/attend/{id}` | HTML swap | Mark event attended |
| `POST /api/profile/*` | Toast | Update profile sections (5 endpoints) |
| `POST /api/sources` | Toast | Add new source |
| `POST /api/sources/{id}/*` | Toast/HTML | Analyze, test, toggle, delete source |
| `GET /api/events` | JSON | Event list for CLI |

## Current State

- **1,496 events** in DB (~1,361 BREC, 55 Lafayette, 48 Eventbrite, 31 AllEvents)
- **2 users** in DB (test accounts)
- **0 custom sources** (generic scraper infrastructure ready)
- All events tagged via heuristics (no OpenAI key configured)
- Web UI: full dark mode, toast notifications, user accounts, profile page
- `ruff check` and `ruff format` pass clean
- Server runs on port 8000 via systemd

## Environment

- Running on exe.dev VM (noon-disk.exe.xyz)
- Port 8000 exposed: <https://noon-disk.exe.xyz:8000/>
- systemd services: `family-events` (web) and `family-events-cron` (scheduler)
- `.env` has secrets only (API keys, sender identities, host/port)
- User-facing settings (notification channels, interests, location) stored per-user in DB
