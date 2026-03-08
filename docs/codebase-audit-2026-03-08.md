# Family Events Codebase Audit — 2026-03-08

## Summary

High-potential product with a solid end-to-end pipeline and unusually polished server-rendered UX for a small project. Main issues are concentrated in maintainability, security hardening, operational robustness, and test coverage rather than outright broken architecture.

## Priority findings

### Critical
- Add CSRF protection to authenticated POST/DELETE routes.
- Fix notification/settings mismatch around Twilio recipient config.
- Replace ad-hoc SQLite migrations with explicit versioned migrations.
- Break up `src/web/app.py` and `src/db/database.py` into smaller modules.

### High
- Add automated tests; current repo effectively has no runnable test suite.
- Move long-running scrape/tag/analysis work off request path into background jobs.
- Improve timezone handling for cron, event windows, and stored datetimes.
- Strengthen auth and input validation on profile/source management flows.

### Medium
- Add better indexes and query strategy for search/filter heavy pages.
- Improve logging/metrics and error visibility.
- Add pagination/limits to JSON APIs and heavy pages.
- Introduce retry/timeouts/rate limiting around external integrations.

### Low
- Clean up docs/runtime drift and reduce reliance on in-memory undo/rate-limit state.
- Add browser/install instructions for local screenshot/UI verification workflows.
- Expand health checks beyond database reachability.
