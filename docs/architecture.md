# Architecture & Data Flow

## System Overview

The system has two entry points: a **web dashboard** (FastAPI) and a **cron scheduler**
(APScheduler). Both share the same pipeline modules and SQLite database.

```mermaid
graph TB
    subgraph Entry Points
        WEB["Web Dashboard<br/>FastAPI :8000"]
        CRON["Cron Scheduler<br/>APScheduler"]
        CLI["CLI<br/>python -m src.main"]
    end

    subgraph Pipeline
        SCR["Scrapers<br/>5 sources"]
        TAG["Tagger<br/>OpenAI / Heuristic"]
        RANK["Ranker<br/>Weighted scoring"]
        WX["Weather Service<br/>OpenWeatherMap"]
        FMT["Formatter<br/>Text message"]
        NOTIFY["Dispatcher<br/>Console/SMS/Telegram/Email"]
    end

    DB[(SQLite Database)]

    CLI --> SCR
    CLI --> TAG
    CLI --> NOTIFY
    WEB -->|"POST /api/scrape"| SCR
    WEB -->|"POST /api/tag"| TAG
    WEB -->|"POST /api/notify"| NOTIFY
    CRON -->|"Daily 2AM"| SCR
    CRON -->|"Daily 2AM"| TAG
    CRON -->|"Friday 8AM"| RANK
    CRON -->|"Friday 8AM"| NOTIFY

    SCR -->|upsert events| DB
    TAG -->|read untagged| DB
    TAG -->|write tags| DB
    RANK -->|read tagged events| DB
    WX --> RANK
    RANK --> FMT
    FMT --> NOTIFY
    WEB -->|read events| DB
```



## Data Pipeline Flow

The full pipeline runs as: **Scrape → Tag → Rank → Notify**.
Each step can also be triggered independently.

```mermaid
sequenceDiagram
    participant S as Scrapers
    participant DB as SQLite
    participant T as Tagger (LLM)
    participant R as Ranker
    participant W as Weather API
    participant F as Formatter
    participant N as Notifier

    Note over S,N: Full Pipeline (daily scrape + Friday notify)

    S->>DB: Upsert ~1,500 events<br/>(dedup by source+source_id)
    Note over S: BREC, Eventbrite,<br/>AllEvents, Lafayette,<br/>Libraries

    DB->>T: Get untagged events
    T->>T: OpenAI gpt-4o-mini<br/>or keyword heuristic
    T->>DB: Save EventTags JSON<br/>(toddler_score, categories, ...)

    Note over R,N: Friday notification flow

    DB->>R: Get weekend events (Sat+Sun)
    W->>R: Weekend forecast<br/>(temp, rain%, UV)
    R->>R: Score each event<br/>(7 weighted factors)
    R->>F: Top 10 ranked events
    F->>N: Formatted message
    N->>N: Route to channels<br/>(console, SMS, Telegram, email)
```



## Scraper Architecture

All scrapers inherit from `BaseScraper` and implement `async scrape() -> list[Event]`.
Each scraper handles its own HTML parsing, deduplication key generation, and error recovery.

```mermaid
classDiagram
    class BaseScraper {
        <<abstract>>
        +name: str
        +scrape() list~Event~*
        #_client() AsyncContextManager
    }

    class BrecScraper {
        +name = "brec"
        -_scrape_month(url)
        -_parse_event_card(el)
    }

    class EventbriteScraper {
        +name = "eventbrite"
        -_scrape_city(city)
        -_parse_jsonld(script)
        -_parse_server_data(script)
    }

    class AllEventsScraper {
        +name = "allevents"
        -_scrape_city(city)
        -_parse_card(el)
    }

    class LafayetteScraper {
        +name = "lafayette"
        -_scrape_mec_calendar(url, venue)
        -_parse_mec_event(el)
    }

    class LibraryScraper {
        +name = "library"
        -_scrape_libcal_rss(url)
    }

    BaseScraper <|-- BrecScraper
    BaseScraper <|-- EventbriteScraper
    BaseScraper <|-- AllEventsScraper
    BaseScraper <|-- LafayetteScraper
    BaseScraper <|-- LibraryScraper
```



## Database Schema

Single table design. Events are uniquely identified by `(source, source_id)`.
Tags are stored as a JSON blob in the `tags` column.

```mermaid
erDiagram
    EVENTS {
        text id PK "UUID"
        text source "brec, eventbrite, ..."
        text source_id "dedup key per source"
        text source_url
        text title
        text description
        text location_name
        text location_address
        text location_city "Lafayette or Baton Rouge"
        real latitude
        real longitude
        text start_time "ISO 8601"
        text end_time "ISO 8601, nullable"
        int is_recurring "0 or 1"
        text recurrence_rule
        int is_free "0 or 1"
        real price_min
        real price_max
        text image_url
        text scraped_at "ISO 8601"
        text raw_data "JSON blob"
        text tags "EventTags JSON, nullable"
        int attended "0 or 1"
    }
```



### EventTags JSON Structure

```json
{
  "toddler_score": 8,
  "age_min_recommended": 0,
  "age_max_recommended": 99,
  "indoor_outdoor": "outdoor",
  "noise_level": "moderate",
  "crowd_level": "medium",
  "energy_level": "active",
  "stroller_friendly": true,
  "parking_available": true,
  "bathroom_accessible": true,
  "food_available": false,
  "nap_compatible": true,
  "weather_dependent": true,
  "good_for_rain": false,
  "good_for_heat": false,
  "categories": ["animals", "nature", "arts"],
  "confidence_score": 0.5,
  "parent_attention_required": "partial",
  "meltdown_risk": "low"
}
```

## Scoring Breakdown

The ranker computes a weighted score for each tagged event. Higher is better.

```mermaid
graph LR
    subgraph Inputs
        TS["Toddler Score<br/>×3.0"]
        IM["Interest Match<br/>×2.5"]
        WC["Weather Compat<br/>×2.0"]
        CP["City Proximity<br/>×2.0"]
        TM["Timing Score<br/>×1.5"]
        LG["Logistics<br/>×1.0"]
        NV["Novelty<br/>×0.5"]
    end

    TS --> SUM(("Sum"))
    IM --> SUM
    WC --> SUM
    CP --> SUM
    TM --> SUM
    LG --> SUM
    NV --> SUM
    SUM --> FINAL["Final Score<br/>0-150+"]

    style TS fill:#6366f1,color:#fff
    style IM fill:#8b5cf6,color:#fff
    style WC fill:#06b6d4,color:#fff
    style CP fill:#10b981,color:#fff
    style TM fill:#f59e0b,color:#fff
    style LG fill:#ef4444,color:#fff
    style NV fill:#6b7280,color:#fff
```




| Factor         | Weight | Source          | How It Works                                                  |
| -------------- | ------ | --------------- | ------------------------------------------------------------- |
| Toddler Score  | ×3.0   | AI tags         | LLM rates 0-10 how appropriate for a 3-year-old               |
| Interest Match | ×2.5   | Tags + Profile  | Compares event categories against loves/likes/dislikes        |
| Weather Compat | ×2.0   | Tags + Forecast | Rain→indoor bonus, heat→shade bonus, outdoor→clear bonus      |
| City Proximity | ×2.0   | Event location  | Lafayette=+10, Baton Rouge=+2, other=-5                       |
| Timing         | ×1.5   | Event time      | Morning bonus, nap time (1-3pm) penalty, post-bedtime penalty |
| Logistics      | ×1.0   | AI tags         | Stroller-friendly, parking, bathrooms, low meltdown risk      |
| Novelty        | ×0.5   | Attended flag   | Not recently attended gets a bonus                            |


## Notification Flow

```mermaid
graph LR
    RANKED["Ranked Events<br/>(top 3)"] --> FMT["Formatter<br/>format_console_message()"]
    FMT --> DISPATCH["Dispatcher"]

    DISPATCH --> CON["Console<br/>stdout"]
    DISPATCH --> SMS["SMS<br/>Twilio"]
    DISPATCH --> TG["Telegram<br/>Bot API"]
    DISPATCH --> EM["Email<br/>Resend"]

    style CON fill:#10b981,color:#fff
    style SMS fill:#6366f1,color:#fff
    style TG fill:#0ea5e9,color:#fff
    style EM fill:#f59e0b,color:#fff
```



The formatter produces a plain-text message like:

```
🌟 Weekend Plans for Your Little One! 🌟

Weather: ⛅ Sat 85°F / 🌤️ Sun 87°F

🥇 TOP PICK: Lafayette Farmers & Artisans Market
   📍 Lafayette | 🕐 Sat 12:00pm | 💵 Free
   ✨ animals, arts, outdoor, stroller-friendly

🥈: Movies at Moncus - Zootopia
   ...
```

## Module Dependency Graph

```mermaid
graph TD
    CONFIG["config.py<br/>Settings"] --> TAGGER
    CONFIG --> WEATHER
    CONFIG --> SMS_N["sms.py"]
    CONFIG --> TG_N["telegram.py"]
    CONFIG --> EM_N["email.py"]
    CONFIG --> DISPATCH

    MODELS["db/models.py<br/>Event, EventTags,<br/>InterestProfile"] --> DATABASE
    MODELS --> SCRAPERS
    MODELS --> TAGGER
    MODELS --> SCORING

    DATABASE["db/database.py<br/>Database"] --> WEB
    DATABASE --> SCHEDULER

    SCRAPERS["scrapers/*<br/>5 scrapers"] --> SCHEDULER
    TAGGER["tagger/llm.py<br/>EventTagger"] --> SCHEDULER
    SCORING["ranker/scoring.py"] --> SCHEDULER
    SCORING --> WEB
    WEATHER["ranker/weather.py<br/>WeatherService"] --> SCHEDULER
    WEATHER --> WEB
    FORMATTER["notifications/formatter.py"] --> SCHEDULER
    FORMATTER --> WEB
    DISPATCH["notifications/dispatcher.py"] --> SCHEDULER
    SMS_N --> DISPATCH
    TG_N --> DISPATCH
    EM_N --> DISPATCH
    CON_N["console.py"] --> DISPATCH

    SCHEDULER["scheduler.py<br/>run_scrape, run_tag,<br/>run_notify"] --> WEB
    SCHEDULER --> CRON
    SCHEDULER --> CLI_M["main.py (CLI)"]

    WEB["web/app.py<br/>FastAPI"]
    CRON["cron.py<br/>APScheduler"]

    style MODELS fill:#6366f1,color:#fff
    style DATABASE fill:#6366f1,color:#fff
    style WEB fill:#10b981,color:#fff
    style SCHEDULER fill:#f59e0b,color:#fff
```



## File Structure

```
family-events/
├── pyproject.toml              # Dependencies, ruff + ty config
├── .env / .env.example         # API keys
├── family_events.db            # SQLite database (auto-created)
├── family-events.service       # systemd: web server
├── family-events-cron.service  # systemd: scheduler
├── src/
│   ├── config.py               # Settings from .env
│   ├── main.py                 # CLI (scrape/tag/notify/serve/events)
│   ├── scheduler.py            # Pipeline orchestrator
│   ├── cron.py                 # APScheduler daemon
│   ├── db/
│   │   ├── models.py           # Event, EventTags, InterestProfile
│   │   └── database.py         # Async SQLite (upsert, search, filter)
│   ├── scrapers/
│   │   ├── base.py             # BaseScraper ABC
│   │   ├── brec.py             # BREC parks
│   │   ├── eventbrite.py       # Eventbrite
│   │   ├── allevents.py        # AllEvents.in
│   │   ├── lafayette.py        # Moncus, Arts, Science Museum
│   │   └── library.py          # Library calendars
│   ├── tagger/
│   │   └── llm.py              # OpenAI + heuristic fallback
│   ├── ranker/
│   │   ├── scoring.py          # Multi-factor weighted scoring
│   │   └── weather.py          # OpenWeatherMap forecasts
│   ├── notifications/
│   │   ├── formatter.py        # Text message formatting
│   │   ├── dispatcher.py       # Channel routing
│   │   ├── console.py          # stdout
│   │   ├── sms.py              # Twilio
│   │   ├── telegram.py         # Telegram Bot
│   │   └── email.py            # Resend
│   └── web/
│       ├── app.py              # FastAPI routes (221 lines)
│       └── templates/          # 14 Jinja2 templates
└── docs/
    ├── architecture.md         # This file
    ├── frontend.md             # HTMX + template docs
    └── pipeline.md             # Scraping + tagging pipeline
```

