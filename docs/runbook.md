# Operator Runbook

This runbook is the practical operating guide for the current MVP deployment shape.

## Current deployment model

The currently supported MVP posture is:

- one FastAPI web process via `family-events.service`
- one scheduler process via `family-events-cron.service`
- one shared Postgres database

This matches the current in-memory coordination assumptions in the app, including rate limiting and the active job registry.

## Prerequisites

The host should have:

- the repo checked out at the deployed path
- `.env` present and readable by the service user
- Postgres reachable through `DATABASE_URL`
- systemd units installed for `family-events` and `family-events-cron`

## Deploy or update the app

Run from the repo root on the host:

```bash
git pull
uv sync
make check
make db-migrate
sudo systemctl restart family-events
sudo systemctl restart family-events-cron
```

Then verify:

```bash
systemctl status family-events --no-pager
systemctl status family-events-cron --no-pager
curl -fsS http://127.0.0.1:8000/health
```

Expected health outcome:

- HTTP `200`
- `"status": "ok"`
- pipeline timestamps present once the app has already run scrape/tag/notify at least once

If `/health` returns `503`, inspect:

```bash
journalctl -u family-events -n 100 --no-pager
journalctl -u family-events-cron -n 100 --no-pager
```

## Run migrations

Apply Alembic migrations explicitly:

```bash
make db-migrate
```

If the web app fails on startup after a schema change, run migrations first, then restart both services.

## Verify the scheduler is alive

Check service state:

```bash
systemctl status family-events-cron --no-pager
journalctl -u family-events-cron -n 50 --no-pager
```

Check app-level health:

```bash
curl -fsS http://127.0.0.1:8000/health | jq
```

What to look for:

- `checks.pipeline.latest_scraped_at` moving after scheduled scrape windows
- `checks.pipeline.latest_tagged_at` moving after scrape runs
- `checks.pipeline.latest_notified_at` moving after Friday notification runs
- `checks.pipeline.stuck_running_jobs` equal to `0`

## Manually trigger the pipeline

### From the web UI

- dashboard: `Scrape + Tag`, `Scrape`, `Tag Untagged`, `Retag Stale`, `Notify`
- jobs page: `/jobs` for user-owned jobs
- shared pipeline history: `/jobs?scope=shared`

### From the CLI

```bash
uv run python -m src.main scrape
uv run python -m src.main tag
uv run python -m src.main notify
uv run python -m src.main pipeline
```

Use `pipeline` for the normal end-to-end operator flow.

## Inspect failed jobs

Start in the UI:

- `/jobs` for your user-owned jobs
- `/jobs?scope=shared` for scheduled/shared pipeline work
- filter `state=failed`

Then inspect logs:

```bash
journalctl -u family-events -n 100 --no-pager
journalctl -u family-events-cron -n 100 --no-pager
```

Useful failure patterns:

- scrape/tag/notify job summaries are stored in the jobs table
- notification jobs store per-channel delivery results
- stale running jobs are auto-failed on startup and stop blocking duplicate prevention

## Inspect failed or stale sources

Start in the UI:

- `/sources` for the user source list
- `/source/{id}` for a specific source detail page and recent source jobs

What source status means right now:

- `active`: recent scrape returned events successfully
- `stale`: scrape succeeded but returned zero events
- `failed`: analysis or scraping failed
- `disabled`: source is turned off
- `analyzing`: recipe analysis/test is in progress

If a source is `failed` or `stale`:

1. Open the source detail page and review recent source jobs.
2. Re-run analysis for custom sources if the recipe is weak or the page changed.
3. Trigger a source test job.
4. Review app logs if the failure is not obvious from the job summary.

## Rotate secrets

Secrets currently live in `.env`.

Typical rotation flow:

```bash
vim .env
sudo systemctl restart family-events
sudo systemctl restart family-events-cron
```

Re-check:

```bash
curl -fsS http://127.0.0.1:8000/health
journalctl -u family-events -n 50 --no-pager
journalctl -u family-events-cron -n 50 --no-pager
```

Rotate at least:

- `SESSION_SECRET`
- `OPENAI_API_KEY`
- `RESEND_API_KEY`
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TELEGRAM_BOT_TOKEN`

## Backup cadence

Minimum recommended MVP cadence:

- nightly Postgres backup
- one backup before every deploy that includes migrations
- one backup before destructive data fixes or bulk imports

## Back up the database

Generic Postgres backup using `DATABASE_URL`:

```bash
mkdir -p backups
pg_dump "$DATABASE_URL" --format=custom --file "backups/family-events-$(date +%F-%H%M%S).dump"
```

If using the local Docker Compose Postgres service:

```bash
mkdir -p backups
docker exec family-events-postgres pg_dump -U family_events -d family_events -Fc \
  > "backups/family-events-$(date +%F-%H%M%S).dump"
```

## Restore the database

Restore into a target database only after stopping the app and taking a fresh safety backup.

Generic restore:

```bash
sudo systemctl stop family-events
sudo systemctl stop family-events-cron
pg_restore --clean --if-exists --no-owner --dbname "$DATABASE_URL" backups/family-events-YYYY-MM-DD-HHMMSS.dump
make db-migrate
sudo systemctl start family-events
sudo systemctl start family-events-cron
```

Local Docker Compose restore:

```bash
sudo systemctl stop family-events
sudo systemctl stop family-events-cron
cat backups/family-events-YYYY-MM-DD-HHMMSS.dump | \
  docker exec -i family-events-postgres pg_restore -U family_events -d family_events --clean --if-exists
make db-migrate
sudo systemctl start family-events
sudo systemctl start family-events-cron
```

After restore, verify `/health`, the jobs UI, and at least one manual page load.

## Quick smoke checklist after deploy or restore

1. `curl -fsS http://127.0.0.1:8000/health`
2. Open `/login`
3. Open `/jobs?scope=shared`
4. Open `/sources`
5. Trigger a manual `Scrape + Tag`
6. Confirm the shared/user job card appears and completes
