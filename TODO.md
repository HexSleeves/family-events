# TODO: Family Events

A prioritized engineering plan based on the current codebase audit.

## P0 — Security and correctness

- [x] Add CSRF protection for all authenticated state-changing routes
  - [x] Generate per-session CSRF token
  - [x] Validate token on all POST/DELETE endpoints
  - [x] Include token in HTMX forms and requests
  - [x] Add origin/referer validation as a secondary safeguard
- [x] Harden authentication flows
  - [x] Add route-specific rate limiting for login and signup
  - [x] Change logout from `GET /logout` to `POST /logout`
  - [x] Improve password validation rules
  - [x] Review session cookie settings for secure deployment
- [x] Fix notification configuration inconsistencies
  - [x] Resolve Twilio recipient mismatch (`twilio_to_number` vs per-user model)
  - [x] Decide whether SMS recipient is global config or stored per user
  - [x] Make config, model, profile UI, and notifier behavior consistent
- [x] Tighten source URL handling
  - [x] Validate URLs before fetch/analyze
  - [x] Block localhost/private-network targets
  - [x] Add request size/time limits for user-submitted sources

## P1 — Reliability and operations

- [x] Move long-running work off the request path
  - [x] Run scrape jobs asynchronously/backgrounded
  - [x] Run tag jobs asynchronously/backgrounded
  - [x] Run source analyze/test asynchronously/backgrounded
  - [x] Add job status tracking visible in UI
  - [x] Prevent duplicate concurrent jobs
- [x] Introduce explicit database migrations
  - [x] Add schema version tracking table
  - [x] Replace silent `ALTER TABLE` migration attempts with versioned migrations
  - [x] Add migration command / startup migration step
- [x] Standardize timezone handling
  - [x] Store and enforce timezone-aware datetimes everywhere
  - [x] Set APScheduler timezone explicitly to `America/Chicago`
  - [x] Review weekend/date-window queries for UTC/local correctness
  - [x] Audit scraper timestamp parsing for timezone consistency
- [x] Improve health and observability
  - [x] Replace `print()`-style operational logs with structured logging
  - [x] Add logging context for user/source/job failures
  - [x] Expand `/health` to include scheduler/job freshness signals
  - [x] Add timing metrics for scrape/tag/notify stages
- [x] Improve resilience of external HTTP calls
  - [x] Centralize `httpx` client configuration
  - [x] Add sane connect/read timeouts everywhere
  - [x] Add retries/backoff for transient failures
  - [x] Add consistent user-agent headers

## P2 — Maintainability and architecture

- [x] Break up `src/web/app.py`
  - [x] Extract auth routes
  - [x] Extract profile routes
  - [x] Extract events routes
  - [x] Extract calendar routes
  - [x] Extract sources routes
  - [x] Extract pipeline/action routes
  - [x] Extract shared response helpers and middleware
- [x] Remove SQLite support and standardize on Postgres
  - [x] Collapse runtime DB selection to Postgres-only
  - [x] Remove SQLite-specific config and dependency paths
  - [x] Move local dev and tests onto Docker Postgres
  - [x] Update docs/tooling away from SQLite-era guidance
- [ ] Break up `src/db/postgres.py`
  - [ ] Extract event repository methods
  - [ ] Extract user repository methods
  - [ ] Extract source repository methods
  - [ ] Extract jobs repository methods
  - [ ] Extract dedupe logic into dedicated module
  - [ ] Keep the public database facade thin and obvious
- [ ] Introduce a service layer between routes and repositories
  - [ ] Event service
  - [ ] Profile service
  - [ ] Source service
  - [ ] Pipeline service
  - [ ] Notification service
- [ ] Clean up API boundary conventions
  - [ ] Separate HTMX fragment endpoints from JSON endpoints more clearly
  - [ ] Remove inline script reload hacks where possible
  - [ ] Standardize toast/event trigger responses

## P3 — Testing and quality gates

- [x] Add `pytest` and test tooling to dev dependencies
- [ ] Make `pytest` part of the standard quality checks
- [ ] Add database tests
  - [ ] Event upsert tests
  - [ ] Dedupe tests
  - [ ] Search/filter/pagination tests
  - [ ] User/source CRUD tests
- [ ] Add route tests
  - [x] Login/signup/logout flows
  - [x] Profile update endpoints
  - [x] Attend/unattend endpoints
  - [x] Source management endpoints
  - [x] Health and page rendering smoke tests
- [ ] Add unit tests for core logic
  - [x] Heuristic tagger
  - [x] Ranking/scoring
  - [ ] Weather summarization
  - [x] Notification dispatch behavior
- [ ] Add regression tests for security-sensitive flows
  - [x] CSRF rejection
  - [x] Unauthorized access rejection
  - [x] Rate-limit behavior

## P4 — Performance and scaling

- [ ] Improve event search performance
  - [ ] Benchmark Postgres trigram/full-text search for title/description queries
  - [ ] Evaluate denormalizing `toddler_score` into a dedicated column
  - [ ] Review sort/filter queries that rely on JSON extraction
- [ ] Add/adjust indexes
  - [ ] Revisit current `tags IS NULL` partial index
  - [ ] Add indexes for `start_time`, `attended`, `location_city`, source/date combinations
  - [ ] Benchmark query performance on larger datasets
- [ ] Optimize tagging throughput
  - [x] Add bounded concurrency for LLM tagging
  - [ ] Add retry behavior for transient LLM failures
  - [ ] Only retag changed/stale events
  - [ ] Store tagging version/model metadata
- [ ] Reduce heavy-page/query load
  - [x] Paginate or limit `/api/events`
  - [ ] Avoid fetching more events than needed on dashboard/detail pages
  - [ ] Cache weather results for a short window

## P5 — Product/data model improvements

- [ ] Improve notification model
  - [ ] Add per-user SMS recipient if SMS is user-configurable
  - [ ] Add delivery result history/logging
  - [ ] Add retry/failure visibility per channel
- [ ] Improve ranking configurability
  - [ ] Centralize scoring weights
  - [ ] Allow ranking versioning/tuning
  - [ ] Persist or expose score explanations more consistently
- [ ] Improve source ingestion UX
  - [ ] Add source analysis history/errors in UI
  - [ ] Add source quotas/limits per user
  - [ ] Add clearer builtin vs custom source presentation
- [ ] Improve event provenance/auditability
  - [ ] Track scrape runs/jobs
  - [ ] Track tag runs/jobs
  - [ ] Track notification runs/jobs
  - [ ] Store more metadata about event merge/dedupe decisions

## P6 — Cleanup and polish

- [ ] Fix docs/runtime drift
  - [x] Update README to match current build/runtime behavior
  - [x] Document Postgres-only local/dev support and remove SQLite migration guidance
  - [ ] Document testing expectations accurately
  - [ ] Clarify library/source support status
- [ ] Clarify local database artifact ownership
  - [x] Remove repo-root SQLite DB expectations from docs/tooling
  - [ ] Decide where local backup/dump files should live outside the repo root
- [x] Split dev vs prod server behavior
  - [x] Disable `reload=True` outside development
  - [x] Add explicit dev/prod serve modes
- [ ] Decide whether in-memory undo/rate-limit state should be ephemeral
  - [ ] If not, persist them in Postgres or another shared store
- [ ] Add browser/UI verification setup notes for this VM environment

## Suggested implementation order

1. CSRF + auth hardening
2. Twilio/config consistency fix
3. Add pytest and baseline tests
4. Background jobs for scrape/tag/source analysis
5. Explicit DB migrations
6. Timezone cleanup
7. Split web app module
8. Split database module
9. Search/index optimizations
10. Product polish and observability improvements
