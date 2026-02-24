# 🌟 Family Events Discovery System

Automated system that scrapes local family events in Lafayette and Baton Rouge, Louisiana,
tags them for toddler-friendliness using AI, and sends curated "Weekend Plans" notifications
every Friday morning.

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

# 5. List upcoming events
uv run python -m src.main events
```

## Architecture

```
Scheduler (Cron)                          Web Admin UI
  Daily 2AM: scrape + tag                 http://localhost:8000
  Friday 8AM: rank + notify               Dashboard / Events / Weekend
       │                                          │
       ▼                                          ▼
┌─────────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│  Scrapers   │→ │ LLM Tag  │→ │ Ranker   │→ │ Notifier │
│             │  │          │  │          │  │          │
│ BREC        │  │ OpenAI   │  │ Score    │  │ Console  │
│ Eventbrite  │  │ or       │  │ Rank     │  │ SMS      │
│ AllEvents   │  │ Heuristic│  │ Weather  │  │ Telegram │
│ Libraries   │  │ Fallback │  │ Interests│  │ Email    │
└─────────────┘  └──────────┘  └──────────┘  └──────────┘
                      │
                ┌─────▼─────┐
                │  SQLite   │
                │  Database  │
                └───────────┘
```

## Data Sources

| Source | Type | Status | Events |
|--------|------|--------|--------|
| BREC (brec.org) | HTML scraping | ✅ Working | ~1,600/month |
| Eventbrite | HTML + JSON-LD | ✅ Working | ~45 |
| AllEvents.in | HTML scraping | ✅ Working | ~30 |
| Lafayette Public Library | LibCal (needs Playwright) | ⏳ Pending | - |
| EBRP Library | LibCal (needs Playwright) | ⏳ Pending | - |
| Lafayette Gov | HTML scraping | ⏳ Needs URL fix | - |
| Facebook Groups | Playwright + auth | 🔮 Future | - |

## Event Scoring

Events are scored on a weighted scale:

```
score = toddler_score × 3.0     (AI: 0-10 how good for a 3yo)
      + interest_match × 2.5     (matches daughter's loves/likes)
      + weather_compat × 2.0     (rain→indoor, heat→shade/water)
      + timing_score × 1.5       (avoid nap time, prefer mornings)
      + logistics × 1.0          (stroller, parking, bathrooms)
      + novelty × 0.5            (haven't attended recently)
```

## Configuration

### Required
- `OPENAI_API_KEY` — For AI event tagging (falls back to heuristic rules without it)

### Optional
- `WEATHER_API_KEY` — OpenWeatherMap for weekend forecasts
- `TWILIO_*` — SMS notifications via Twilio
- `TELEGRAM_*` — Telegram bot notifications
- `RESEND_API_KEY` — Email notifications via Resend
- `NOTIFICATION_CHANNELS` — `["console", "sms", "telegram", "email"]`

## Web Admin UI

- **Dashboard** — Stats, action buttons, top events
- **Events** — All events table with scores and tags
- **Weekend** — Ranked weekend picks with notification preview
- **Event Detail** — Full AI tags, raw data, mark attended

## CLI Commands

```bash
uv run python -m src.main scrape           # Run all scrapers
uv run python -m src.main tag              # Tag untagged events with LLM
uv run python -m src.main notify --name Em # Send notification
uv run python -m src.main pipeline --name Em # Full pipeline
uv run python -m src.main events           # List upcoming events
uv run python -m src.main serve            # Start web server
```

## Deployment

### Systemd Services

```bash
# Web server
sudo cp family-events.service /etc/systemd/system/
sudo systemctl enable --now family-events

# Cron scheduler (scrape daily, notify Fridays)
sudo cp family-events-cron.service /etc/systemd/system/
sudo systemctl enable --now family-events-cron
```

## Daughter's Interest Profile

Configured in `src/db/models.py` → `InterestProfile`:

- **Loves:** animals, playground, water play, music, trains, messy art
- **Likes:** nature walks, story time, dancing
- **Dislikes:** loud crowds, sitting still, dark spaces
- **Constraints:** 45 min max drive, nap 1-3pm, bedtime 7:30pm, $30 budget

## Development

```bash
# Install dev tools
uv sync --group dev

# Format
ruff format src/

# Lint
ruff check src/ --fix

# Type check
ty check
```

## Tech Stack

- **Python 3.12** + **uv** package manager
- **FastAPI** web framework
- **SQLite** database (WAL mode)
- **httpx** + **BeautifulSoup** for scraping
- **OpenAI** API for event tagging (gpt-4o-mini)
- **APScheduler** for cron jobs
- **Pydantic v2** for data models
- **ruff** for formatting + linting
- **ty** for type checking
