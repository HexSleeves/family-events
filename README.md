# 🌟 Family Events Discovery System

Automated system that scrapes local family events in Lafayette and Baton Rouge, Louisiana,
tags them for toddler-friendliness using AI, scores and ranks them by personalized criteria,
and sends curated "Weekend Plans" notifications every Friday morning.

Built for a parent of a 3-year-old who wants to stop doom-scrolling Facebook groups
for things to do this weekend.

**Live:** [https://noon-disk.exe.xyz:8000/](https://noon-disk.exe.xyz:8000/)

![Dashboard](docs/screenshots/dashboard.png)

## How It Works

```
1. SCRAPE  →  2. TAG  →  3. RANK  →  4. NOTIFY
```

1. **Scrape** — Pulls events from 5 sources (BREC parks, Eventbrite, AllEvents, Lafayette venues, libraries)
2. **Tag** — Hybrid rule + AI tagging assigns structured family-fit metadata, signal lists, and a toddler score
3. **Rank** — Weighted scoring blends intrinsic toddler fit, personalized interests, weather, timing, logistics, and penalties
4. **Notify** — Sends top weekend picks via console, SMS, Telegram, or email

The web dashboard lets you browse, search, filter, and manually trigger any step.

## Quick Start

```bash
# 1. Clone and install
cd family-events
uv sync

# 2. Configure
cp .env.example .env
# Edit .env with your API keys (see Configuration below)

# 3. Run the pipeline
uv run python -m src.main scrape    # Scrape all sources
uv run python -m src.main tag       # Tag events with AI
uv run python -m src.main notify    # Send weekend notification
uv run python -m src.main pipeline  # All three in one

# 4. Start the web UI
uv run python -m src.main serve     # http://localhost:8000

# 5. List upcoming events in terminal
uv run python -m src.main events
```

## Web Dashboard

The dashboard is a server-rendered web app using **Jinja2** templates, **HTMX** for
interactivity, and **Tailwind CSS** for styling. No JavaScript framework, no build step.

| Page | Description |
|------|-------------|
| **Dashboard** (`/`) | Stats overview, action buttons (scrape/tag/notify), top 5 toddler-friendly events |
| **Events** (`/events`) | Searchable, filterable, paginated table of all events (25/page) |
| **Event Detail** (`/event/{id}`) | Full event info, AI tags grid, raw scraped data, mark attended |
| **Weekend** (`/weekend`) | Ranked weekend picks with weather forecast, notification preview |

### Events Page Features
- **Search** — Full-text search across titles and descriptions (300ms debounce)
- **Filters** — City, source, tagged/untagged, minimum toddler score
- **Sort** — Date, score, title (ascending/descending)
- **Pagination** — Server-side, 25 per page, bookmarkable URLs
- **Loading skeletons** — Shimmer animations during all data loads

All interactions use HTMX — no page reloads, URL updates via `hx-push-url`.

## Data Sources

| Source | Type | Region | Events | Status |
|--------|------|--------|--------|--------|
| [BREC](https://www.brec.org) | HTML scraping | Baton Rouge | ~1,400/month | ✅ Working |
| [Eventbrite](https://www.eventbrite.com) | JSON-LD + HTML | Both cities | ~50 | ✅ Working |
| [AllEvents.in](https://allevents.in) | HTML scraping | Both cities | ~30 | ✅ Working |
| Lafayette venues | MEC WordPress plugin | Lafayette | ~55 | ✅ Working |
| Libraries | LibCal RSS | Both cities | Varies | ⏳ Needs Playwright |

Lafayette venues include Moncus Park, Acadiana Center for the Arts, and Lafayette Science Museum.

## Event Scoring

Each event gets a weighted score combining intrinsic family-fit rules with personal preferences:

```
Final Score = toddler_fit      × 2.2   (normalized 0-10 toddler score)
            + intrinsic_fit    × 0.35  (rule-system raw score, normalized from 0-100)
            + interest_match   × 1.4   (matches child's loves/likes)
            + weather_compat   × 1.0   (rain→indoor, heat→shade/water)
            + timing_score     × 1.0   (morning bonus, nap/bedtime penalty)
            + logistics        × 0.9   (stroller, parking, bathrooms, food)
            + city_proximity   × 0.8   (preferred cities favored)
            + novelty          × 0.4   (haven't attended recently)
            + confidence       × 0.5   (better-described events rank more reliably)
            - rule_penalty           (adult-skewed/high-risk/exclusion signals)
            - budget_penalty         (over-budget events are penalized, not auto-zeroed)
```

### Tagging Rules (per event)

Every event starts at a neutral `raw_rule_score` of **50/100**.

- **Positive signals** add points: toddler/preschool wording, story time, sensory play, playgrounds, animals, water play, family framing, morning timing.
- **Caution signals** subtract points: festival/fair scale, lectures, late evening starts, loud/crowded framing, downtown logistics.
- **Exclusion signals** subtract heavily: bars, breweries, wine/beer, trivia, networking, adults-only, 21+, marathon/5k.
- A derived **audience** is assigned: `toddler_focused`, `family_mixed`, `general_public`, or `adult_skewed`.
- Reasons are preserved on the tag object as `positive_signals`, `caution_signals`, and `exclusion_signals`.

### AI Tags (per event)

| Tag | Values |
|-----|--------|
| `toddler_score` | 0-10 |
| `indoor_outdoor` | indoor / outdoor / both |
| `noise_level` | quiet / moderate / loud |
| `crowd_level` | small / medium / large |
| `energy_level` | calm / moderate / active |
| `meltdown_risk` | low / medium / high |
| `stroller_friendly` | ✅ / ❌ |
| `parking_available` | ✅ / ❌ |
| `bathroom_accessible` | ✅ / ❌ |
| `nap_compatible` | ✅ / ❌ |
| `weather_dependent` | ✅ / ❌ |
| `categories` | e.g., ["animals", "nature", "arts"] |
| `audience` | toddler_focused / family_mixed / general_public / adult_skewed |
| `raw_rule_score` | 0-100 |
| `positive_signals` | reasons the event looks good for toddlers |
| `caution_signals` | softer concerns that reduce the score |
| `exclusion_signals` | strong reasons to avoid recommending it |

### Child Interest Profile

Configured in `src/db/models.py` → `InterestProfile`:

- **Loves:** animals, playground, water play, music, trains, messy art
- **Likes:** nature walks, story time, dancing
- **Dislikes:** loud crowds, sitting still, dark spaces
- **Constraints:** 45 min max drive, nap 1-3pm, bedtime 7:30pm, $30/event budget

## Configuration

Copy `.env.example` to `.env` and configure:

### Required
| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | For AI event tagging (falls back to keyword heuristics without it) |
| `DATABASE_URL` | Database connection string. Use `sqlite+aiosqlite:///family_events.db` locally; target `postgresql+asyncpg://...` in production. |

### Optional
| Variable | Description |
|----------|-------------|
| `WEATHER_API_KEY` | OpenWeatherMap — for weekend forecasts (defaults to typical Louisiana weather) |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_FROM_NUMBER` | SMS sender configuration (recipient is set per user in profile) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Telegram bot notifications |
| `RESEND_API_KEY` / `EMAIL_FROM` | Email sender configuration (recipient is set per user in profile) |
| `SESSION_SECRET` | Required for signed session cookies |
| `APP_BASE_URL` | Public HTTPS origin used for same-origin CSRF checks behind a proxy |
| `SESSION_COOKIE_SECURE` / `SESSION_COOKIE_SAME_SITE` / `SESSION_COOKIE_DOMAIN` / `SESSION_MAX_AGE_SECONDS` | Session cookie hardening controls |

## Architecture

See [docs/architecture.md](docs/architecture.md) for detailed architecture documentation
with Mermaid diagrams.

```
Scheduler (Cron)                          Web Admin UI
  Daily 2AM: scrape + tag                 https://noon-disk.exe.xyz:8000
  Friday 8AM: rank + notify               Jinja2 + HTMX + Tailwind
       │                                          │
       ▼                                          ▼
┌─────────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│  Scrapers   │→ │ LLM Tag  │→ │ Ranker   │→ │ Notifier │
│             │  │          │  │          │  │          │
│ BREC        │  │ OpenAI   │  │ Score    │  │ Console  │
│ Eventbrite  │  │ or       │  │ Rank     │  │ SMS      │
│ AllEvents   │  │ Heuristic│  │ Weather  │  │ Telegram │
│ Lafayette   │  │ Fallback │  │ Interests│  │ Email    │
│ Libraries   │  │          │  │          │  │          │
└─────────────┘  └──────────┘  └──────────┘  └──────────┘
                      │
                ┌─────▼─────┐
                │  SQLite   │
                │  Database  │
                └───────────┘
```

## CLI Commands

```bash
uv run python -m src.main scrape              # Run all scrapers
uv run python -m src.main tag                 # Tag untagged events with LLM
uv run python -m src.main notify --name Em    # Send notification
uv run python -m src.main pipeline --name Em  # Full pipeline
uv run python -m src.main events              # List upcoming events
uv run python -m src.main serve               # Start web server (port 8000)
```

## Deployment

Current production runs on an [exe.dev](https://exe.dev) VM with two systemd services.
The codebase is being prepared for a Postgres-backed deployment via `DATABASE_URL`,
which will enable managed platforms like Render more cleanly.

Runs on an [exe.dev](https://exe.dev) VM with two systemd services:

```bash
# Web server (always running)
sudo systemctl enable --now family-events

# Cron scheduler (daily scrape, Friday notify)
sudo systemctl enable --now family-events-cron
```

## Development

```bash
uv sync                      # Install dependencies
uv run ruff format src/      # Format code
uv run ruff check src/ --fix # Lint + auto-fix
uv run ty check              # Type check
```

All three must pass clean before committing.

## Tech Stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.12 |
| Package manager | uv |
| Web framework | FastAPI + Uvicorn |
| Templates | Jinja2 |
| Frontend interactivity | HTMX 2.0.4 |
| CSS | Tailwind CSS v4 (CLI build) |
| Database | SQLite (WAL mode, aiosqlite) |
| Scraping | httpx + BeautifulSoup4 |
| AI tagging | OpenAI API (gpt-4o-mini) |
| Scheduling | APScheduler |
| Data validation | Pydantic v2 |
| Code quality | ruff (format + lint), ty (type check) |

## Documentation

- [Architecture & Data Flow](docs/architecture.md) — System design, Mermaid diagrams
- [Web Frontend](docs/frontend.md) — HTMX patterns, template structure, skeleton loading
- [Scraping & Tagging Pipeline](docs/pipeline.md) — How events are scraped, tagged, and scored

## License

Personal project. Not licensed for redistribution.
