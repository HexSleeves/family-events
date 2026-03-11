# Migration Notes

This file previously described a planned implementation for generic scraping.
That feature is now implemented, so this document now tracks the **actual major
migration work completed in the repository**.

## 1. Local/dev database migration to Postgres

The project moved from SQLite-first local development to a **Postgres-native**
local workflow.

### What changed

- added `docker-compose.yml` with local Postgres
- added Makefile helpers for DB lifecycle
- updated `.env.example` to default to Postgres
- updated runtime to use `DATABASE_URL`
- fixed Alembic async URL handling
- added initial Postgres Alembic revision
- upgraded schema from SQLite-shaped columns to Postgres-native types

### Local default connection

App/runtime URL:

```text
postgresql+asyncpg://family_events:family_events@localhost:5433/family_events
```

GUI client URL:

```text
postgresql://family_events:family_events@127.0.0.1:5433/family_events
```

### Local workflow

```bash
make db-up
make db-migrate
uv run python -m src.main serve
```

Useful helpers:

```bash
make db-down
make db-logs
make db-reset
```

## 2. Schema migration details

The current Alembic base revision is:

- `91dae90b6493_create_initial_postgres_schema.py`

### Postgres-native upgrades included

- `UUID` PKs with `gen_random_uuid()` defaults
- `CITEXT` emails for users
- `JSONB` event tags and profile-like data
- foreign keys for user/source/job relationships
- `CHECK` constraints for text enums
- expression indexes for tag queries
- trigram GIN indexes for search
- partial indexes for untagged events and toddler score

### Core tables

- `users`
- `sources`
- `events`
- `jobs`
- `alembic_version`

## 3. DB abstraction migration

The app no longer hardcodes SQLite creation in runtime paths.

`src/db/database.py` now exposes:

- `SqliteDatabase`
- `PostgresDatabase` via import from `src/db/postgres.py`
- `create_database(...)`

This allows:

- Postgres in app/local/prod runtime
- SQLite in explicit tests or compatibility scenarios

## 4. UUID compatibility work

The app layer still largely expects string IDs. The Postgres layer therefore:

- accepts UUID-like strings as parameters
- normalizes returned UUID values back to strings
- keeps higher-level code mostly backend-agnostic

Important helpers:

- `_uuid_param(...)`
- `_normalize_uuid(...)`

## 5. SQLite to Postgres one-time migration utility

A one-shot script exists at:

- `scripts/migrate_sqlite_to_postgres.py`

It was built for data carry-over verification, but the current local path is a
**fresh-start Postgres database** because the old local SQLite database had no
meaningful data.

## 6. Generic scraper/source migration

Another major evolution since the original repo shape:

- `sources` became a real persisted model/table
- predefined built-in sources are stored per user
- custom sources can be analyzed and replayed with recipes
- source operations use persisted background jobs

Key files:

- `src/scrapers/router.py`
- `src/scrapers/recipe.py`
- `src/scrapers/generic.py`
- `src/scrapers/analyzer.py`
- `src/web/routes/sources.py`
- `src/predefined_sources.py`

## 7. Runtime behavior changes to know about

### Working

- local Docker Postgres startup
- Alembic migrations
- CRUD against users/sources/jobs/events on Postgres
- scrape/tag/notify flows against Postgres
- tests and ruff after migration fixes

### Still noteworthy

- Postgres startup now fails fast if Alembic migrations have not been applied to the connected database
- search behavior still deserves more end-to-end inspection
- SQLite remains supported in code, but is no longer the primary documented local path

## 8. Recommended docs/userspace guidance

When writing or updating docs elsewhere in the repo:

- describe Postgres as the default local/dev database
- refer to SQLite as compatibility/test fallback only
- document `pipeline` as the normal CLI ingestion flow
- note that `scrape`, `tag`, and `notify` still exist as standalone operator commands
- mention `sources` and `jobs` as first-class product concepts
- avoid calling the generic scraper work a future plan
