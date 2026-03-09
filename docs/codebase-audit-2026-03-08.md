# Family Events Codebase Audit — 2026-03-08

This audit has been refreshed to reflect the current repository state after the
Postgres-native local/dev migration and the generic source-management work.

## Summary

The codebase now has a stronger operational foundation than earlier snapshots:

- Postgres-native local development is in place
- Alembic migrations exist and apply cleanly
- tests and ruff are passing
- authenticated profile/source flows are real product features
- background job persistence exists for long-running UI operations

The biggest remaining concerns are now concentrated in code organization,
end-to-end verification, and a few known functional/runtime gaps.

## Priority findings

### Critical

- Fix the stale CLI surface: `src.main pipeline` still calls missing `run_full_pipeline()`.
- Continue end-to-end verification of Postgres search behavior and important web flows.
- Split up oversized modules, especially `src/web/app.py` and `src/db/database.py` / `src/db/postgres.py`.

### High

- Add more integration tests for Postgres-backed search, filtering, and onboarding/source flows.
- Harden scheduler/runtime timezone behavior; comments mention Central time, but scheduling code does not document timezone configuration clearly.
- Reduce duplicated backend logic between SQLite and Postgres implementations where feasible.
- Expand health/ops visibility beyond a single DB check.

### Medium

- Improve search quality and ranking validation with real smoke tests.
- Strengthen observability around scraping failures, tagging failures, and notification outcomes.
- Consider moving more long-running tasks behind a more durable worker model if usage grows.
- Review generic scraper robustness for JS-heavy or layout-unstable pages.

### Low

- Continue reducing docs/runtime drift as the project evolves quickly.
- Add explicit runbooks for local web smoke testing and browser checks.
- Revisit in-memory rate-limit and undo stores if multi-process deployment becomes normal.

## What improved since the earlier audit

- CSRF protection now exists on authenticated state-changing routes.
- Notification recipient config is now aligned around per-user `email_to` / `sms_to` fields.
- Versioned migrations now exist for Postgres via Alembic.
- There is a real runnable test suite in the repo, and it currently passes.
- Source management is no longer aspirational; it is implemented.

## Recommended next steps

1. Fix or remove the broken `pipeline` CLI command.
2. Add high-value integration tests for Postgres search and weekend recommendation flows.
3. Run a targeted manual smoke pass through signup, onboarding, sources, scrape, tag, search, and notify.
4. Continue decomposing large modules to make future changes safer.
