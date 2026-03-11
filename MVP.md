# MVP Release Plan

This document is the release checklist for taking Family Events from a private/dev-quality app to a public MVP.

The goal is not "perfect architecture." The goal is a stable, understandable, operable product that can safely run in public with scheduled scraping, manual scraping, tagging after scraping, and enough observability to debug issues quickly.

## Current status (2026-03-10)

Already completed in the codebase:

- Public signup and login with CSRF protection, local-dev session handling, and actionable duplicate-account flows
- Per-user onboarding/profile settings, starter source seeding, and authenticated source management
- First-class `Scrape + Tag` pipeline flow shared by dashboard actions and cron
- Signup now triggers a shared system-owned initial `Scrape + Tag` run when starter sources are seeded
- Dashboard/profile now show an initial-import-in-progress state with a direct link to shared pipeline job history
- Persisted background jobs with duplicate prevention, stale-job recovery, cancellation, progress payloads, and job history UI
- Explicit dev vs prod serve commands (`serve-dev` vs `serve`) with production `reload=False`
- Explicit Postgres migration flow with Alembic and `make db-migrate`
- Central `America/Chicago` timezone helpers used by weekend selection, calendar boundaries, cron, and notify flows
- `/health` pipeline freshness reporting for scrape/tag/notify plus stuck-job detection
- Structured notification dispatch results and explicit unknown-channel failures
- Shared outbound HTTP client behavior across scrapers, analyzer, weather, and notification providers
- Authenticated, paginated, rate-limited `/api/events` contract documented in `README.md`

Still open before a true public MVP:

- Benchmarking and search/index work for realistic scale
- Final production deployment/security verification
- Backup/restore runbook and broader operator documentation
- Remaining warning cleanup and some maintainability refactors

---

## Product goals for MVP

The MVP should support:

- Public signup and login
- Per-user profile/onboarding and preferences
- Manual source management
- Manual event pipeline execution from the web UI
- Scheduled scraping on cron
- Tagging immediately after scraping
- Manual notification runs
- Reliable browsing of events, weekend picks, calendars, and attendance
- Enough health/status visibility to know whether the system is working

---

## Non-goals for MVP

These are valuable, but not required before public launch:

- Full multi-process or multi-node scaling
- Perfect service/repository architecture
- Advanced analytics dashboards
- Rich audit/event sourcing for every action
- Fancy retry orchestration across distributed workers
- Highly dynamic user-configurable ranking weights

---

## MVP release criteria

We should consider the app MVP-ready only when all of these are true:

1. Scheduled scraping runs automatically in production.
2. Tagging runs automatically after scraping.
3. Manual "Scrape + Tag" exists in the UI as the primary operator action.
4. Background jobs are visible for both manual and scheduled runs.
5. Production serving does not use autoreload.
6. Database migrations are explicit and safe.
7. Timezone behavior is deterministic and documented.
8. Health checks expose pipeline freshness.
9. Notification delivery results are visible enough to debug failures.
10. Search and key pages remain fast on realistic data volume.
11. Deployment/docs match actual runtime behavior.

### Status

- [x] 1. Scheduled scraping runs automatically in production.
- [x] 2. Tagging runs automatically after scraping.
- [x] 3. Manual "Scrape + Tag" exists in the UI as the primary operator action.
- [x] 4. Background jobs are visible for both manual and scheduled runs.
- [x] 5. Production serving does not use autoreload.
- [x] 6. Database migrations are explicit and safe.
- [ ] 7. Timezone behavior is deterministic and documented.
- [x] 8. Health checks expose pipeline freshness.
- [x] 9. Notification delivery results are visible enough to debug failures.
- [ ] 10. Search and key pages remain fast on realistic data volume.
- [ ] 11. Deployment/docs match actual runtime behavior.

---

# Phase 0 — Finalize MVP scope

## 0.1 Decide product posture

Choose and document one clear target for launch:

- Single-user personal deployment
- Small invite-only beta
- Public multi-user signup

### Tasks
- [ ] Decide target launch mode
- [ ] Document expected user count and expected event volume
- [ ] Document whether custom sources are allowed for all users or admin-only
- [ ] Document whether notifications are user-facing MVP functionality or admin/testing only

### Why this matters
Several technical decisions depend on this:
- rate limits
- source quotas
- auth assumptions
- whether unauthenticated APIs are acceptable
- how much durability/observability we need for jobs

---

# Phase 1 — Release blockers

## 1.1 Fix production serve mode

`src/main.py` currently starts uvicorn with `reload=True`, which is dev behavior and should not be used in production.

### Tasks
- [x] Add explicit dev/prod serve behavior
- [x] Make production serve run with `reload=False`
- [x] Keep local development autoreload in `make dev` or a dedicated dev serve mode
- [x] Update service files and README to reflect correct production startup
- [ ] Add a small test or verification note to ensure prod mode does not reload

### Deliverable
Production service starts without autoreload and docs clearly separate dev and prod startup.

---

## 1.2 Make scheduled pipeline a first-class concept

Today cron runs scrape and tag in sequence, but the codebase still treats scrape and tag mostly as separate manual actions.

### Tasks
- [x] Introduce a first-class pipeline runner for `scrape_then_tag`
- [x] Make cron use the same pipeline runner as the manual UI action
- [x] Add a primary dashboard action for `Scrape + Tag`
- [ ] Keep standalone scrape and standalone tag only if there is a clear operator need
- [x] Add a dedicated job kind for pipeline runs, not just separate scrape/tag jobs
- [x] Define job labels and summaries clearly, e.g.:
  - `Scrape + tag job`
  - `142 events scraped · 121 tagged · 3 failed`

### Deliverable
There is one clear path for the normal ingestion workflow: scrape first, then tag.

---

## 1.3 Harden cron/scheduler for production

`src/cron.py` works, but it is too thin for public release.

### Tasks
- [x] Set APScheduler timezone explicitly to `America/Chicago`
- [x] Replace `print()` logging with structured logging
- [x] Log start, success, duration, and failure of each scheduled run
- [x] Persist scheduled runs in the jobs table so they show in the UI/history
- [x] Add distinct job keys for scheduled jobs vs manual jobs
- [x] Decide whether scheduled jobs are owned by a synthetic system user or a nullable owner model
- [x] Ensure failed scheduled jobs are visible and not silent
- [ ] Review restart behavior for missed runs after downtime
- [ ] Confirm systemd unit behavior and restart policy are appropriate

### Deliverable
Scheduled runs are observable, timezone-correct, and share the same execution path as manual runs.

---

## 1.4 Introduce explicit database migrations

Current startup migration behavior in `src/db/database.py` uses best-effort `ALTER TABLE` calls with suppressed exceptions. That is too fragile for public release.

### Tasks
- [x] Add a schema version table
- [x] Create an explicit migration runner
- [ ] Move bootstrap schema creation into a clear migration/bootstrap layer
- [ ] Replace silent `ALTER TABLE ...` attempts with ordered versioned migrations
- [x] Add a command for running migrations explicitly
- [x] Decide whether app startup should fail when migrations are pending or apply them automatically
- [ ] Add tests covering migration from an older schema state
- [x] Document migration procedure for deployment

### Deliverable
Schema changes are explicit, reviewable, testable, and reproducible.

---

## 1.5 Audit and standardize timezone behavior

The app stores UTC in many places, but weekend logic and cron comments assume local Central time. We need one consistent rule.

### Tasks
- [ ] Define canonical storage policy: store datetimes in UTC
- [ ] Define display/query policy: convert date-window logic through `America/Chicago`
- [ ] Audit weekend selection logic in `src/web/app.py` and `src/scheduler.py`
- [ ] Audit event date parsing in all scrapers
- [ ] Audit notification scheduling assumptions
- [ ] Audit calendar month/day boundaries
- [ ] Ensure weather lookups match local weekend dates
- [ ] Add tests around boundary times near midnight and DST transitions
- [ ] Document the timezone policy in README or architecture docs

### Deliverable
Weekend pages, notifications, scheduled runs, and event queries behave predictably in Central time.

---

## 1.6 Bound or formalize public API surfaces

`/api/events` currently returns recent events without pagination and without a clearly defined public contract.

### Tasks
- [x] Decide whether `/api/events` is public, authenticated, or internal-only
- [ ] If public, add pagination, limits, and rate limiting
- [x] If internal-only, require auth or remove it
- [x] Document the contract if the endpoint remains
- [ ] Audit other endpoints for accidental public exposure

### Deliverable
Public-facing APIs are intentional, bounded, and documented.

---

# Phase 2 — Pipeline reliability and operations

## 2.1 Unify manual and scheduled pipeline execution

### Tasks
- [ ] Refactor pipeline execution into reusable functions/services:
  - [x] scrape sources
  - [x] tag untagged/stale events
  - [x] notify users
  - [x] scrape then tag
  - [ ] scrape then tag then notify
- [ ] Remove duplicated orchestration logic between CLI, web, and cron
- [x] Ensure each entry point uses the same code path
- [x] Standardize result payloads for job summaries

### Deliverable
CLI, web jobs, and cron all reuse the same pipeline orchestration layer.

---

## 2.2 Improve persisted job model and history

The jobs system is already useful. For public release it should become the main operational source of truth.

### Tasks
- [ ] Review whether all long-running actions are persisted as jobs
- [x] Add job summaries that operators can understand quickly
- [x] Ensure progress payloads are structured consistently
- [x] Add support for system/scheduled jobs in history views
- [ ] Expose started/finished durations clearly
- [ ] Add filters for scheduled/manual/system jobs if needed
- [x] Ensure stale running jobs are marked failed on startup
- [x] Add tests for duplicate job prevention and stale job recovery

### Deliverable
Operators can answer: what ran, when, how long it took, and whether it succeeded.

---

## 2.3 Expand `/health` to include freshness and pipeline status

Current health only checks DB reachability and latest scrape timestamp.

### Tasks
- [x] Include last successful scrape time
- [x] Include last successful tag time
- [x] Include last successful notify time
- [x] Include whether any jobs are currently stuck/running too long
- [ ] Include basic source freshness summary if practical
- [ ] Decide whether `/health` is for machine checks only or also human ops
- [ ] Add tests for degraded states

### Deliverable
`/health` tells us whether the app is alive and whether the pipeline is fresh.

---

## 2.4 Centralize external HTTP client behavior

HTTP behavior is scattered between scrapers, weather, and analyzer.

### Tasks
- [x] Introduce a shared HTTP client factory/helper
- [x] Standardize headers/user-agent
- [x] Standardize connect/read/write timeouts
- [x] Add retries/backoff for transient errors where appropriate
- [x] Add consistent error logging context (source URL, service, timeout)
- [x] Audit weather, analyzer, and all scrapers to use the shared helper

### Deliverable
External HTTP calls behave consistently and fail in visible ways.

---

## 2.5 Improve notification delivery visibility

`NotificationDispatcher` currently returns booleans, which is not enough for public debugging.

### Tasks
- [x] Define a richer notification result model:
  - [x] channel
  - [x] success/failure
  - [x] error message
  - [x] recipient used
  - [x] timestamp
- [x] Persist notification job details/results
- [ ] Show notification results in job history or notification-specific UI
- [x] Make unknown notification channels explicit failures, not quiet prints
- [x] Add tests for successful and failed deliveries

### Deliverable
When a notification fails, we can see why without digging through logs only.

---

# Phase 3 — Performance and data quality

## 3.1 Benchmark realistic event volume

Before public launch, we need to know whether key pages stay responsive with larger data.

### Tasks
- [ ] Estimate expected event volume per month
- [ ] Seed a realistic database snapshot for profiling
- [ ] Benchmark:
  - [ ] dashboard
  - [ ] `/events`
  - [ ] `/weekend`
  - [ ] calendar page
  - [ ] source detail page
- [ ] Record current slow queries

### Deliverable
We know where performance is good enough and where it is not.

---

## 3.2 Improve event search performance

Current search uses `LIKE` and JSON extraction, which will degrade.

### Tasks
- [ ] Evaluate SQLite FTS5 for title/description search
- [ ] Consider denormalizing toddler score into a dedicated column
- [ ] Review filter/sort query patterns for indexability
- [ ] Add indexes for common filters:
  - [ ] `start_time`
  - [ ] `attended`
  - [ ] `location_city`
  - [ ] source/date combinations
- [ ] Revisit the current tags partial index
- [ ] Add tests around search correctness after changes

### Deliverable
The events page remains fast and accurate at MVP scale.

---

## 3.3 Review tagger throughput and failure behavior

Tagging is central to product quality and currently depends on batch processing and OpenAI behavior.

### Tasks
- [ ] Confirm tagger concurrency defaults are safe
- [ ] Confirm OpenAI timeout/retry behavior is sane
- [ ] Decide how stale retagging should work operationally
- [ ] Ensure failed tags are visible in job summaries
- [ ] Store/tag model and tagging version consistently
- [ ] Add tests for partial batch failures and recovery

### Deliverable
Tagging is observable and efficient enough for ongoing operation.

---

## 3.4 Review data freshness and stale-source handling

### Tasks
- [ ] Define what `active`, `stale`, `failed`, and `disabled` mean operationally
- [ ] Review source status transitions after zero-result scrapes
- [ ] Decide whether repeated zero-result scrapes should degrade confidence or alert operators
- [ ] Expose stale/failed source visibility more clearly in the UI
- [ ] Add tests for source status transitions

### Deliverable
Source health is understandable and stale sources are visible.

---

# Phase 4 — Refactor for maintainability before public iteration

## 4.1 Split `src/web/app.py`

It is still too large and mixes multiple concerns.

### Tasks
- [x] Extract events routes
- [x] Extract calendar routes
- [x] Extract pipeline/job routes
- [x] Keep dashboard/error handlers in a smaller root module
- [ ] Standardize shared route helpers and context builders
- [ ] Add route tests as modules are moved

### Deliverable
Web routes are easier to reason about and safer to change post-launch.

---

## 4.2 Split `src/db/database.py`

This module currently does schema bootstrapping, row mapping, repositories, job persistence, and dedupe logic.

### Tasks
- [x] Extract schema/bootstrap/migration code
- [ ] Extract event repository methods
- [ ] Extract user repository methods
- [ ] Extract source repository methods
- [ ] Extract jobs repository methods
- [ ] Extract dedupe helpers into a dedicated module
- [ ] Keep the public database interface thin and obvious

### Deliverable
The persistence layer is easier to test and evolve.

---

## 4.3 Add a thin service/orchestration layer

### Tasks
- [ ] Add pipeline service
- [ ] Add source service where appropriate
- [ ] Add notification service result shaping
- [ ] Keep routes focused on request/response concerns only

### Deliverable
Business logic is not spread across routes, cron, and CLI entry points.

---

# Phase 5 — UX and public-facing cleanup

## 5.1 Make the main operator actions clearer

The UI should guide the operator toward the normal workflow.

### Tasks
- [x] Promote `Scrape + Tag` as the primary ingest action on dashboard
- [ ] Clarify when standalone `Tag` is useful
- [ ] Clarify when `Notify` is useful
- [x] Add lightweight operator copy on dashboard/job cards
- [x] Ensure job panels refresh consistently after pipeline actions

### Deliverable
The intended workflow is obvious without reading the code.

---

## 5.2 Finish remaining HTMX consistency and warning cleanup

### Tasks
- [ ] Eliminate remaining `TemplateResponse` deprecation warnings
- [ ] Audit remaining pages/helpers for non-HTMX-first interactions
- [ ] Remove remaining inline handler leftovers if any exist
- [x] Ensure error rerenders work consistently with HTMX targets

### Deliverable
The UI is clean, consistent, and warning-free.

---

## 5.3 Improve empty/error states for public users

### Tasks
- [ ] Audit dashboard empty states
- [ ] Audit weekend page empty states
- [ ] Audit source failures and job failure presentation
- [ ] Make public-facing messages actionable and non-technical where appropriate

### Deliverable
Users understand what to do next when there is no data or a failure occurs.

---

# Phase 6 — Security and public deployment review

## 6.1 Final security review

Security basics are much better now, but a public launch should still get a final pass.

### Tasks
- [ ] Verify session cookie settings in production environment
- [ ] Verify HTTPS and reverse proxy forwarding behavior
- [ ] Verify `APP_BASE_URL` and origin checks in real deployment
- [ ] Review unauthenticated routes and APIs
- [ ] Review source submission abuse potential and quotas
- [ ] Review login/signup rate limits against expected public traffic
- [ ] Review secret handling for OpenAI, Resend, Twilio, Telegram

### Deliverable
Public deployment assumptions are verified, not guessed.

---

## 6.2 Decide how much multi-process safety MVP needs

Some state is still in-memory:
- rate limiting store
- bulk unattend undo store
- active job registry

### Tasks
- [ ] Decide whether MVP is strictly single-process on one VM
- [ ] If yes, document that clearly
- [ ] If no, move volatile coordination state into SQLite or another shared store
- [ ] Ensure deployment architecture matches this decision

### Deliverable
No hidden mismatch between runtime architecture and code assumptions.

---

## 6.3 Add operational backups and restore notes

SQLite can be fine for MVP, but only if we treat it responsibly.

### Tasks
- [ ] Move runtime DB path out of repo root if needed
- [ ] Define backup cadence
- [ ] Define restore process
- [ ] Document WAL file handling and deployment expectations
- [ ] Add a simple operator runbook for DB maintenance

### Deliverable
Data durability is adequate for an MVP product.

---

# Phase 7 — Testing and release gates

## 7.1 Expand automated tests where current risk is highest

### Tasks
- [ ] Add route tests for profile update endpoints
- [x] Add route tests for scrape/tag/notify job endpoints
- [x] Add tests for pipeline job execution and duplicate prevention
- [ ] Add migration tests
- [x] Add tests for timezone/weekend boundary behavior
- [x] Add tests for notification dispatch result handling
- [x] Add tests for search correctness after performance changes
- [ ] Add source status transition tests

### Deliverable
The highest-risk release areas have regression coverage.

---

## 7.2 Define release quality gates

### Tasks
- [x] Define required pre-release commands, at minimum:
  - [x] `uv run ruff check src tests`
  - [x] `uv run pytest`
  - [x] `uv run ty check`
- [ ] Add smoke-test checklist for deployed environment
- [ ] Add browser/manual verification checklist for key flows
- [ ] Decide whether every release requires a seeded-data UI review

### Deliverable
Release readiness is a repeatable process, not a vibe.

---

# Phase 8 — Documentation and launch prep

## 8.1 Fix docs/runtime drift

The README and deployment docs need to match reality before launch.

### Tasks
- [x] Update README to reflect actual architecture and current behavior
- [x] Document background jobs in the web UI
- [x] Document cron/scheduled pipeline behavior
- [x] Document dev vs prod server startup
- [x] Document migration flow
- [ ] Document backup/recovery notes
- [x] Document environment variables clearly

### Deliverable
A new maintainer can deploy and operate the app from the docs.

---

## 8.2 Add an operator runbook

### Tasks
- [ ] How to deploy
- [ ] How to run migrations
- [ ] How to verify scheduler is alive
- [ ] How to manually trigger scrape/tag/notify
- [ ] How to inspect failed jobs
- [ ] How to inspect failed sources
- [ ] How to rotate secrets
- [ ] How to back up/restore the DB

### Deliverable
The app is operable by someone other than the current developer.

---

# Suggested implementation order

## Sprint 1 — Release blockers
- [x] Fix prod serve mode
- [x] Add explicit scheduler timezone
- [x] Create first-class `scrape_then_tag` pipeline path
- [x] Make scheduled runs persisted/visible as jobs
- [x] Expand `/health` with freshness signals

## Sprint 2 — Safety and correctness
- [ ] Add explicit migrations
- [ ] Audit timezone handling
- [ ] Bound `/api/events` and other public endpoints
- [ ] Improve notification result persistence

## Sprint 3 — Performance and cleanup
- [ ] Benchmark realistic data volume
- [ ] Improve search/indexing
- [ ] Centralize HTTP client behavior
- [ ] Clean remaining `TemplateResponse` warnings

## Sprint 4 — Refactor and docs
- [ ] Split `web/app.py`
- [ ] Split `db/database.py`
- [ ] Update README, deployment docs, and operator runbook
- [ ] Final smoke tests on deployed environment

---

# Concrete backlog by area

## Pipeline and scheduling
- [x] Add `run_scrape_then_tag(...)`
- [ ] Add `run_scrape_tag_notify(...)` if needed
- [x] Add job kind for pipeline runs
- [x] Reuse same pipeline from CLI, UI, and cron
- [x] Scheduled jobs visible in jobs UI

## Web UI
- [x] Dashboard primary action becomes `Scrape + Tag`
- [x] Jobs page includes scheduled/system jobs
- [x] Improve job result summaries
- [ ] Finish HTMX consistency sweep
- [ ] Remove remaining deprecation warnings

## Database
- [ ] Schema version table
- [ ] Migration runner
- [ ] Search/index improvements
- [ ] Consider denormalized toddler score column
- [ ] Review runtime DB file placement

## Notifications
- [x] Persist notification outcomes
- [x] Make unknown channel a hard failure path
- [ ] Surface results in jobs/history

## Operations
- [x] Structured logging
- [x] Pipeline freshness in `/health`
- [ ] Backup and restore procedure
- [ ] Production service docs

## Tests
- [x] Pipeline job tests
- [x] Scheduler tests or scheduler-adjacent orchestration tests
- [ ] Migration tests
- [x] Timezone boundary tests
- [x] Search performance correctness tests

---

# Definition of done for launch

We are ready to launch the MVP when:

- [ ] Production serve path is correct
- [x] Scheduled scrape + tag runs reliably
- [x] Manual scrape + tag exists and is the main path
- [x] Scheduled and manual jobs are visible in the UI
- [ ] Migrations are explicit and tested
- [ ] Timezone behavior is documented and covered by tests
- [x] `/health` includes freshness information
- [x] Notification failures are diagnosable
- [ ] Search remains responsive on realistic data
- [ ] Remaining warning/deprecation cleanup is done
- [ ] Docs and runbook are complete
- [x] Full lint/test/type-check pass is green

---

# Recommended immediate next step

Start with a focused release-hardening pass:

1. Fix prod serve mode
2. Introduce first-class `Scrape + Tag`
3. Make cron use that same path
4. Persist scheduled runs in jobs/history
5. Expand `/health`

That gives the highest MVP value quickly and aligns the product with the intended workflow.
