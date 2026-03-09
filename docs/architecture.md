# Architecture & Data Flow

## Overview

Family Events is a FastAPI app plus an APScheduler worker sharing one database.
The project now targets **PostgreSQL first** for local and deployed environments,
with SQLite retained only as a compatibility/testing path through the
`create_database(...)` factory.

Primary entry points:

- **Web app**: `uv run python -m src.main serve`
- **CLI**: `uv run python -m src.main ...`
- **Scheduler**: `uv run python -m src.cron`

```mermaid
graph TB
    subgraph Entry Points
        WEB["FastAPI Web UI"]
        CLI["CLI\npython -m src.main"]
        CRON["APScheduler\npython -m src.cron"]
    end

    subgraph Core Services
        DBAPI["DB abstraction\ncreate_database(...)"]
        SCRAPE["Scrapers\nbuilt-in + generic"]
        TAG["Tagger\nOpenAI or heuristic"]
        RANK["Ranker\nscore_event_breakdown"]
        NOTIFY["Notification dispatcher"]
    end

    PG[(PostgreSQL)]

    WEB --> DBAPI
    WEB --> SCRAPE
    WEB --> TAG
    WEB --> RANK
    WEB --> NOTIFY

    CLI --> SCRAPE
    CLI --> TAG
    CLI --> RANK
    CLI --> NOTIFY

    CRON --> SCRAPE
    CRON --> TAG
    CRON --> NOTIFY

    DBAPI --> PG
    SCRAPE --> DBAPI
    TAG --> DBAPI
    RANK --> DBAPI
    NOTIFY --> DBAPI
```

## Runtime Topology

### Web app

`src/web/app.py` owns:

- page routes (`/`, `/events`, `/weekend`, `/calendar`, `/jobs`)
- action endpoints (`/api/scrape`, `/api/tag`, `/api/notify`, `/api/dedupe`)
- event attendance APIs
- health endpoint
- session middleware and app lifespan DB wiring

Feature routers live in:

- `src/web/routes/auth.py`
- `src/web/routes/profile.py`
- `src/web/routes/sources.py`

### Scheduler

`src/cron.py` runs two scheduled flows:

- daily scrape + tag
- Friday morning notifications per user

### Shared orchestration

`src/scheduler.py` provides the reusable pipeline functions:

- `run_scrape()`
- `run_tag()`
- `run_scrape_then_tag()`
- `run_notify()`

`src.main pipeline` now runs the normal scrape+tag flow followed by notify.

## Database Architecture

## Backend selection

`src/db/database.py` exposes a backend-agnostic factory:

- Postgres when `DATABASE_URL` is a Postgres URL
- SQLite when explicitly passed a SQLite path/URL

This preserves simple SQLite-backed tests while letting app/runtime use Postgres.

## Postgres schema

Alembic owns the Postgres schema via revision:

- `alembic/versions/91dae90b6493_create_initial_postgres_schema.py`

Key Postgres-native choices:

- `UUID` primary keys with `gen_random_uuid()`
- `CITEXT` for `users.email`
- `JSONB` for tags and profile-like structures
- foreign keys across `users`, `sources`, and `jobs`
- `CHECK` constraints for enum-like text columns
- trigram indexes for title/description search
- JSON expression indexes for tag queries

```mermaid
erDiagram
    USERS ||--o{ SOURCES : owns
    USERS ||--o{ JOBS : owns
    SOURCES ||--o{ JOBS : related_to

    USERS {
        uuid id PK
        citext email UK
        text display_name
        jsonb preferred_cities
        jsonb notification_channels
        jsonb interest_profile
        text theme
        text child_name
        bool onboarding_complete
    }

    SOURCES {
        uuid id PK
        uuid user_id FK
        text name
        text url UK
        text domain
        text city
        text category
        bool builtin
        bool enabled
        text status
        text recipe_json
        timestamptz last_scraped_at
        int last_event_count
    }

    EVENTS {
        uuid id PK
        text source
        text source_id
        text source_url
        text title
        text description
        text location_city
        timestamptz start_time
        timestamptz end_time
        jsonb raw_data
        jsonb tags
        jsonb score_breakdown
        bool attended
    }

    JOBS {
        uuid id PK
        uuid owner_user_id FK
        uuid source_id FK
        text kind
        text job_key
        text state
        text detail
        text result_json
    }
```

## Data flow

```mermaid
sequenceDiagram
    participant U as User / Cron / CLI
    participant S as Scraper layer
    participant D as Database
    participant T as Tagger
    participant R as Ranker
    participant N as Dispatcher

    U->>S: run_scrape()
    S->>D: upsert events
    U->>T: run_tag()
    T->>D: fetch untagged or stale-tagged events
    T->>D: write tags + score_breakdown
    U->>R: run_notify()
    R->>D: fetch weekend events
    R->>N: formatted ranked message
```

## Scraper model

There are two source classes in practice:

1. **Predefined built-in sources** stored in `sources` with `builtin=True`
2. **User-added custom sources** that use `recipe_json` plus `GenericScraper`

Built-in routing is handled by `src/scrapers/router.py` using the source URL's
normalized domain.

## Search behavior

Search is implemented in the DB layer:

- SQLite uses `LIKE`
- Postgres uses `ILIKE`
- Postgres also has trigram indexes to support better title/description lookup

The repository still merits additional end-to-end validation of search ranking
and results behavior, but the storage/indexing side is now Postgres-native.

## Jobs and background work

Long-running web-triggered operations create persisted job records in `jobs`.
The app restores/fails stale running jobs at startup.

This is used for flows like:

- source analysis
- source test runs
- scrape/tag/notify actions from the UI

## Security and request model

Current web protections include:

- session middleware with configurable cookie settings
- CSRF token checks on authenticated state-changing routes
- same-origin aware configuration via `APP_BASE_URL`
- simple in-memory rate limiting

## Local development architecture

Default local path:

- Docker Compose Postgres on host port `5433`
- app `DATABASE_URL=postgresql+asyncpg://family_events:family_events@localhost:5433/family_events`
- Alembic migrations applied with `uv run alembic upgrade head`

Useful make targets:

- `make db-up`
- `make db-down`
- `make db-reset`
- `make db-migrate`

## Timezone policy

- Persist timestamps in UTC.
- Evaluate weekend and calendar date windows in `America/Chicago`.
- Convert event datetimes to the app timezone before deriving local dates for weekend selection, calendar grouping, and duplicate fingerprints.
