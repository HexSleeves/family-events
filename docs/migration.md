# Migration Notes

This document records the completed move to a **Postgres-only supported
runtime** for Family Events.

## 1. Supported database posture

Local development and deployed environments now use PostgreSQL only.

The supported local workflow is:

```bash
make db-up
make db-migrate
make dev
```

Canonical local connection URLs:

```text
postgresql+asyncpg://family_events:family_events@localhost:5433/family_events
postgresql://family_events:family_events@127.0.0.1:5433/family_events
```

Useful local DB helpers:

```bash
make db-down
make db-logs
make db-reset
```

## 2. Schema state

Alembic owns the schema. The initial Postgres base revision is:

- `91dae90b6493_create_initial_postgres_schema.py`

Important Postgres-native choices:

- `UUID` primary keys with `gen_random_uuid()` defaults
- `CITEXT` emails for users
- `JSONB` storage for tags and profile-like fields
- foreign keys across `users`, `sources`, and `jobs`
- `CHECK` constraints for enum-like fields
- trigram indexes for title/description search
- JSON expression indexes for tag-driven filtering

Core tables:

- `users`
- `sources`
- `events`
- `jobs`
- `alembic_version`

## 3. Runtime and operator expectations

- `DATABASE_URL` should point at PostgreSQL in all supported environments.
- App startup expects the connected database to already be at Alembic head.
- `pipeline` is the normal CLI ingestion path; `scrape`, `tag`, and `notify`
  remain available as focused operator commands.
- `sources` and `jobs` are first-class persisted product concepts.

## 4. Legacy SQLite cleanup

This cut removes the remaining SQLite-era developer guidance from the repo's
owned docs and tooling surface:

- local development no longer documents SQLite as a fallback
- `aiosqlite` has been dropped from `pyproject.toml`
- `scripts/migrate_sqlite_to_postgres.py` has been removed

There is no longer a supported in-repo SQLite-to-Postgres migration path. For
local development, start from a fresh Docker Compose Postgres database instead.
