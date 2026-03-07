# TODO: Family Events

## Completed ✅

### Security & Reliability
- [x] Require `SESSION_SECRET`
- [x] Source management auth + ownership checks
- [x] Rate limiting on high-impact POST routes
- [x] Health endpoint (`GET /health`)
- [x] Request logging middleware
- [x] Friendly `404` / `500` pages

### Data Quality
- [x] Cross-source fuzzy dedupe during ingest
- [x] Dedupe debug logging (`DEDUP_DEBUG`)
- [x] Backfill dedupe CLI command (`uv run python -m src.main dedupe`)
- [x] Admin dedupe action button

### Attended Flow
- [x] Attended tab (`/events?attended=yes`)
- [x] Attend / unattend from event detail
- [x] Attended badge on cards
- [x] Bulk unattend in attended view
- [x] Undo bulk unattend via toast action

### UX Quality-of-Life
- [x] Event detail: maps link, share button, score breakdown, related events, collapsible raw data
- [x] Discover: near-you section + pipeline timestamps + action auto-refresh
- [x] Weekend: backend weather summary/tips as single source of truth

### UI Modernization Sprint
- [x] UI baseline cleanup commit
- [x] Base layout modernization commit
- [x] Events + attended modernization commit

---

## Backlog Ideas (Not In Current TODO Scope)
- Events-by-day chart / mini calendar
- Weekend map view + ICS export + time-slot planner
- More discover shelves (music/sports/free)
- Notification history + richer delivery channels
- Auth tier upgrades (email verification, forgot password)
- Backup automation + retention policy
