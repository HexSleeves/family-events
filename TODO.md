# TODO: Family Events

## Completed

### ✅ Refactor Frontend to Jinja2 + HTMX + Tailwind CSS

- Replaced all inline HTML-in-Python-f-strings with 14 Jinja2 template files
- `base.html` with shared layout, nav, Tailwind CDN, HTMX CDN
- 4 page templates: dashboard, events, event_detail, weekend
- 8 partials: _event_card, _event_row, _events_table, _tags_grid, _stats,_notification, _skeleton_table, _skeleton_action
- `app.py` is now pure route handlers (221 lines, zero HTML)
- All API endpoints return HTML snippets for HTMX (JSON API preserved at `/api/events`)
- `_page()` helper and all inline CSS deleted

### ✅ Events Page: Pagination, Search, Filtering

- `Database.search_events()` with SQL-level LIKE search, filters, pagination
- `Database.get_filter_options()` for dynamic dropdown values
- Search bar with 300ms debounced `keyup` via HTMX
- Filter dropdowns: city, source, tagged/untagged, min score, sort
- Server-side pagination (25/page) with `hx-push-url` for bookmarkable URLs
- HTMX partial rendering: detects `HX-Request` header, returns only table+pagination
- "Clear filters" link when any filter is active

### ✅ Loading Skeleton Animations

- Global 3px indeterminate progress bar at top of viewport
- Events table: full skeleton overlay with 8 shimmer rows, fake header, pagination pills
- Dashboard: CSS spinner on action buttons + disabled state during requests
- Event detail: spinner on "Mark Attended" button
- Skeleton CSS system: `@keyframes shimmer`, `.skeleton`, `.spinner`, `.skeleton-overlay`

---

## Up Next

### Generic Scraper with Smart Routing

**Design doc:** [docs/design-generic-scraper.md](docs/design-generic-scraper.md)

Let users add any event website URL. The system auto-generates a scraping recipe
via LLM (once), then replays it with BeautifulSoup on every scrape ($0 per run).
Built-in scrapers handle known domains; unknown domains get the generic path.

**Phases:**

1. Foundation — Recipe models, Source model, sources table, domain router
2. Generic Scraper — CSS/JSON-LD replay engine, LLM analyzer + validator
3. Scheduler — Include user sources in `run_scrape()`, stale detection
4. Web UI — Sources page (list, add, test, detail, enable/disable, delete)
5. Polish — Built-in source display, edge cases, confidence display

### Mobile Responsive Layout

The current layout works on desktop but needs attention on mobile:

- Header nav collapses to hamburger menu on small screens
- Events table becomes card-based layout on mobile (table unreadable on narrow screens)
- Filter row wraps properly (it does via `flex-wrap` but dropdowns may be too wide)
- Pagination buttons get touch-friendly sizing
- Test on 375px viewport width

### Tailwind Production Build

Currently using the Tailwind CDN play script (~115KB runtime compiler). For production:

- Install Tailwind CLI standalone binary
- Extract classes to `static/styles.css` via `tailwindcss -o static/styles.css --minify`
- Replace CDN `<script>` with `<link>` to static CSS file
- Expect ~10-15KB compiled CSS vs 115KB CDN runtime

### Event Detail Improvements

- Collapsible raw data section (currently always open, can be long)
- Link to Google Maps for event location
- "Share this event" button (copy link)
- Show related/similar events at bottom
- Show event scoring breakdown (why this score?)

### Dashboard Improvements

- After action buttons complete, auto-refresh stats (currently stale until page reload)
- Add "last scraped" and "last tagged" timestamps
- Show events by day chart or mini calendar
- Quick filter shortcuts ("Lafayette only", "This weekend", "Free events")

### Weekend Page Improvements

- Map view of weekend events (Leaflet.js)
- Export to calendar (.ics download)
- Weather-based recommendations ("It's rainy — here are indoor picks")
- Time-slot planner (morning vs afternoon suggestions avoiding nap time)

### Data & Scraping

- Configure OpenAI API key for real LLM tagging (currently all heuristic)
- Add Playwright-based scrapers for library sites (LibCal needs JS rendering)
- Fix Lafayette Gov scraper URL
- Add Facebook Groups scraper (Playwright + auth)
- De-duplicate events across sources (same event on Eventbrite and AllEvents)
- Scrape event images for card thumbnails

### Notifications

- Configure at least one real notification channel (Telegram is easiest)
- Email digest with HTML formatting (Resend)
- Notification preferences UI (which channels, which days, score threshold)
- "Snooze" or "Not interested" on individual events

### Infrastructure

- Add health check endpoint (`GET /health`)
- Request logging middleware (log slow queries)
- Error pages (404, 500) with proper templates instead of raw FastAPI errors
- Database backup cron job
- Rate limiting on API endpoints
