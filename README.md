# 🌟 Family Events Discovery System

Family Events is a FastAPI app that helps parents discover local child-friendly
activities, score them for toddler fit, and generate weekend recommendations.

It currently targets **PostgreSQL-first local development** and supports:

- scraping built-in and custom event sources
- AI/heuristic event tagging
- personalized ranking
- web-based source/profile management
- console, SMS, Telegram, and email notifications

Built for a parent of a 3-year-old who wants to stop doom-scrolling Facebook
groups for things to do this weekend.

**Live:** [https://noon-disk.exe.xyz:8000/](https://noon-disk.exe.xyz:8000/)

![Dashboard](docs/screenshots/dashboard.png)

## How It Works

```text
scrape -> tag -> rank -> notify
```

1. **Scrape** — loads enabled saved sources and upserts events into the database
2. **Tag** — assigns structured toddler-fit metadata with OpenAI or heuristics
3. **Rank** — blends toddler fit, interests, weather, timing, logistics, and penalties
4. **Notify** — formats and sends a weekend plan through user-configured channels

The web app also lets users:

- sign up and seed predefined sources
- manage custom scraping sources
- browse/search/filter events
- mark events attended
- inspect and rerun background jobs

## Quick Start

```bash
# 1. Clone and install
cd family-events
uv sync

# 2. Configure
cp .env.example .env
# Set at minimum: OPENAI_API_KEY and SESSION_SECRET

# 3. Start local Postgres and apply schema
make db-up
make db-migrate

# 4. Start the web app
uv run python -m src.main serve-dev
# Open http://localhost:8000 and sign up

# 5. Seed data through normal app flows
# Signup creates predefined sources for the new user
uv run python -m src.main pipeline

# 6. Optional: inspect upcoming events in the terminal
uv run python -m src.main events
```

## Local Development Database

Local development now defaults to Docker Compose Postgres.

App/runtime URL:

```text
postgresql+asyncpg://family_events:family_events@localhost:5433/family_events
```

GUI client URL:

```text
postgresql://family_events:family_events@127.0.0.1:5433/family_events
```

Useful DB commands:

```bash
make db-up
make db-down
make db-logs
make db-reset
make db-migrate
```

## Web App Overview

The frontend is server-rendered with:

- **FastAPI**
- **Jinja2**
- **HTMX**
- **Tailwind CSS** (compiled stylesheet in `src/web/static/styles.css`)

There is no SPA framework.

### Main pages

| Page | Description |
|------|-------------|
| `/` | Dashboard with event stats, recent pipeline info, category slices, and action controls |
| `/events` | Search/filter/sort/paginate events |
| `/event/{id}` | Full event detail with tags and raw source data |
| `/weekend` | Ranked recommendations for the upcoming weekend |
| `/calendar` / `/calendars` | Calendar views |
| `/calendar.ics` | ICS feed |
| `/sources` | Source catalog and custom source management |
| `/source/{id}` | Source detail, recipe, sample events, recent jobs |
| `/jobs` | Background job history |
| `/profile` | Onboarding/profile/theme/notification/password settings |
| `/login` / `/signup` | Auth flows |

### Events page features

- search across title and description
- city/source/tagged/attended filters
- minimum toddler score filter
- server-side pagination
- HTMX partial updates
- loading skeletons and action indicators

## Data Sources

### Built-in source families

| Source | Type | Region |
|--------|------|--------|
| [BREC](https://www.brec.org) | HTML scraping | Baton Rouge |
| [Eventbrite](https://www.eventbrite.com) | JSON-LD + HTML | Multiple cities |
| [AllEvents.in](https://allevents.in) | HTML + structured data | Multiple cities |
| Moncus Park / ACA / Lafayette Science Museum | MEC / venue-specific | Lafayette |
| LibCal library feeds | RSS / HTML fallback | Lafayette + Baton Rouge |

### Source management model

Sources are persisted in the `sources` table and may be:

- **predefined built-in sources**
- **custom user-added sources** with stored `ScrapeRecipe` JSON

Custom sources can be:

- analyzed
- re-analyzed
- tested
- enabled/disabled
- deleted

## Event Scoring

Ranking is implemented in `src/ranker/scoring.py`.

Current weighted components:

```text
final score =
    toddler_fit   * 2.2
  + intrinsic     * 0.35
  + interest      * 1.4
  + weather       * 1.0
  + timing        * 1.0
  + logistics     * 0.9
  + novelty       * 0.4
  + city          * 0.8
  + confidence    * 0.5
  - budget_penalty
  - rule_penalty
```

### Signals included in tags

The current tag model includes fields such as:

- `tagging_version`
- `toddler_score`
- `indoor_outdoor`
- `noise_level`
- `crowd_level`
- `energy_level`
- `stroller_friendly`
- `parking_available`
- `bathroom_accessible`
- `food_available`
- `nap_compatible`
- `weather_dependent`
- `good_for_rain`
- `good_for_heat`
- `confidence_score`
- `parent_attention_required`
- `meltdown_risk`
- `audience`
- `positive_signals`
- `caution_signals`
- `exclusion_signals`
- `raw_rule_score`

## Configuration

Copy `.env.example` to `.env`.

### Required

| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | Enables LLM tagging. Without it, the app falls back to heuristics. |
| `DATABASE_URL` | Defaults to local Docker Postgres. |
| `SESSION_SECRET` | Required for the web app to start. |

### Common optional settings

| Variable | Description |
|----------|-------------|
| `WEATHER_API_KEY` | OpenWeatherMap forecast lookup |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_FROM_NUMBER` | SMS sender config |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Telegram bot config |
| `RESEND_API_KEY` / `EMAIL_FROM` | Email sender config |
| `APP_BASE_URL` | Public origin used for same-origin checks behind a proxy |
| `SESSION_COOKIE_SECURE` / `SESSION_COOKIE_SAME_SITE` / `SESSION_COOKIE_DOMAIN` / `SESSION_MAX_AGE_SECONDS` | Session cookie controls |
| `TAGGER_CONCURRENCY` / `TAGGER_BATCH_SIZE` | LLM tagging throughput tuning |
| `BACKGROUND_JOB_TIMEOUT_SECONDS` | Job stale-failure threshold |

See `.env.example` for the current canonical local/dev defaults.

## Database

The app now runs primarily on **PostgreSQL**.

The Postgres schema includes:

- `UUID` primary keys with `gen_random_uuid()` defaults
- `CITEXT` user emails
- `JSONB` tags and profile-like blobs
- foreign keys across `users`, `sources`, and `jobs`
- `CHECK` constraints for enum-like fields
- trigram search indexes on title/description
- JSON expression indexes for tag filters

SQLite is still supported in code for compatibility/tests, but it is no longer
the primary documented local path.

## CLI Commands

```bash
uv run python -m src.main scrape
uv run python -m src.main tag
uv run python -m src.main notify
uv run python -m src.main events
uv run python -m src.main serve      # production-style, no autoreload
uv run python -m src.main serve-dev  # local development with autoreload
uv run python -m src.main dedupe
```

### `pipeline` CLI command

The CLI now exposes a working pipeline command:

```bash
uv run python -m src.main pipeline
```

That runs the normal scrape+tag flow first, then sends notifications.

## Scheduler

Run the APScheduler worker with:

```bash
uv run python -m src.cron
```

Current scheduled behavior:

- daily scrape + tag via the same shared pipeline path used by the dashboard
- Friday morning notifications for each user

The scheduler runs in `America/Chicago` and logs start/success/failure with duration.
All canonical storage timestamps remain UTC, while weekend/date-window behavior is evaluated in `America/Chicago` so late-night UTC boundaries and DST changes map to the intended local day.
Scheduled scrape + tag runs are also persisted in the jobs table under the synthetic system user.
Notification jobs now store per-channel delivery results so failures are visible in job history.

## Deployment

Current production runs on an [exe.dev](https://exe.dev) VM with systemd services.
The runtime now cleanly supports Postgres through `DATABASE_URL`.

Production serving should use `uv run python -m src.main serve`, which runs
without autoreload. Local development autoreload is intentionally separate via
`uv run python -m src.main serve-dev` or `make dev`.

Example service management:

```bash
sudo systemctl enable --now family-events
sudo systemctl enable --now family-events-cron
```

## API surface

The app's `/api/*` routes are internal UI endpoints, not a public integration API.
For MVP, `/api/events` is authenticated and bounded:

- requires a logged-in session
- rate limited like other internal API routes
- supports pagination via `page` and `per_page`
- max `per_page` is 100
- supports the same basic filters as the events page: `q`, `city`, `source`, `tagged`, `attended`, `score_min`, and `sort`

Current `/api/events` response shape:

```json
{
  "items": [
    {
      "id": "...",
      "title": "...",
      "source": "...",
      "city": "...",
      "start_time": "2025-01-01T10:00:00+00:00",
      "tagged": true,
      "toddler_score": 8,
      "attended": false
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 25,
    "total": 42,
    "total_pages": 2
  },
  "filters": {
    "q": "",
    "city": "",
    "source": "",
    "tagged": "",
    "attended": "",
    "score_min": null,
    "sort": "start_time"
  }
}
```

## Legacy SQLite -> Postgres migration

A one-time migration helper still exists at:

- `scripts/migrate_sqlite_to_postgres.py`

That script is now mainly for legacy recovery/reference. The recommended local
workflow is a **fresh Postgres start**.

If you intentionally need the legacy migration path:

```bash
export DATABASE_URL='postgresql+asyncpg://family_events:family_events@localhost:5433/family_events'
make db-up
make db-migrate
uv run python scripts/migrate_sqlite_to_postgres.py \
  --sqlite-path family_events.db \
  --postgres-url "$DATABASE_URL"
```

## Development

### Python checks

```bash
uv run ruff check src tests alembic scripts
uv run pytest -q
```

### Formatting and lint helpers

```bash
make lint
make format
make test
make check
```

### CSS build

The repo includes Tailwind CLI scripts:

```bash
npm install
npm run css:build
npm run css:watch
```

## Tech Stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.12 |
| Package manager | uv |
| Web framework | FastAPI + Uvicorn |
| Templates | Jinja2 |
| Frontend interactivity | HTMX 2.0.4 |
| CSS | Tailwind CSS via CLI build |
| Database | PostgreSQL 16 + asyncpg + SQLAlchemy + Alembic |
| SQLite fallback | aiosqlite |
| Scraping | httpx + BeautifulSoup4 |
| AI tagging | OpenAI API (`gpt-4o-mini` by default) |
| Scheduling | APScheduler |

## More Documentation

- [docs/architecture.md](docs/architecture.md)
- [docs/pipeline.md](docs/pipeline.md)
- [docs/frontend.md](docs/frontend.md)
- [docs/design-generic-scraper.md](docs/design-generic-scraper.md)
- [docs/migration.md](docs/migration.md)


## External HTTP behavior

Outbound HTTP calls now use a shared client helper with:
- a standardized user-agent/header baseline
- centralized connect/read timeout defaults
- retry/backoff for transient GET failures
- consistent structured logging context for remote failures

This shared behavior is used by scrapers, page analysis, weather, and notification delivery integrations.
