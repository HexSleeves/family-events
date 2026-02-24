# TODO: Family Events

## Completed

### ✅ Refactor Frontend to Jinja2 + HTMX + Tailwind CSS

- 20 Jinja2 templates (10 pages + 10 partials)
- `base.html` shared layout with nav, Tailwind CDN, HTMX CDN
- `app.py` is pure route handlers (594 lines)
- All API endpoints return toast or HTML snippets for HTMX

### ✅ Events Page: Pagination, Search, Filtering

- SQL-level LIKE search, filter by city/source/tagged/score, sort
- 300ms debounced search via HTMX `keyup changed delay:300ms`
- Server-side pagination (25/page) with `hx-push-url` for bookmarkable URLs
- HTMX partial rendering: `HX-Request` header → returns only table+pagination

### ✅ Loading Skeleton Animations

- Global 3px indeterminate progress bar
- Events table: full skeleton overlay with 8 shimmer rows
- Action buttons: CSS spinner + disabled state during requests

### ✅ Generic Scraper with Smart Routing

- Users add any event website URL
- LLM auto-generates ScrapeRecipe (CSS selectors or JSON-LD)
- GenericScraper replays recipe with BeautifulSoup ($0 per run)
- Domain router dispatches built-in vs generic scrapers
- Sources page: add, test, re-analyze, enable/disable, delete

### ✅ User Accounts & Profile

- Signup, login, logout with bcrypt + signed session cookies
- Profile page with HTMX-powered sections:
  - Appearance (light/dark/system theme)
  - Location (home city, preferred cities)
  - Child preferences (loves, likes, dislikes, constraints)
  - Notification channel selection
  - User's sources list
  - Password change
- Nav bar adapts to auth state (login/signup vs user/logout)

### ✅ Dark Mode

- Tailwind `darkMode: 'class'` with per-user theme preference
- Auto mode respects `prefers-color-scheme`
- All 20 templates have `dark:` variant classes

### ✅ Toast Notifications

- Replaced all inline success/error HTML with toast system
- `_toast()` server helper returns `HX-Trigger` header
- Client JS parses header via `htmx:afterRequest` event
- 4 variants: success (green), error (red), warning (amber), info (blue)
- Slide-in animation, 3.5s auto-dismiss, click to dismiss
- Inline styles for dynamic elements (Tailwind CDN can't JIT them)

### ✅ Per-User Settings

- Notification channels, email_to, child_name stored per-user
- Interest profile (loves/likes/dislikes/constraints) per-user
- Weekend ranking + notifications use logged-in user's profile
- .env retains only secrets and infrastructure config

---

## Up Next

### Mobile Responsive Layout

- Header nav collapses to hamburger menu on small screens
- Events table becomes card-based layout on mobile
- Filter row wraps properly on narrow screens
- Pagination buttons get touch-friendly sizing
- Test on 375px viewport width

### Tailwind Production Build

- Install Tailwind CLI standalone binary
- Extract to `static/styles.css` via `tailwindcss -o static/styles.css --minify`
- Replace CDN `<script>` with `<link>` to static CSS
- Expected ~10-15KB compiled CSS vs ~115KB CDN runtime

### Event Detail Improvements

- Collapsible raw data section
- Google Maps link for event location
- "Share this event" button (copy link)
- Related/similar events at bottom
- Scoring breakdown (why this score?)

### Dashboard Improvements

- Auto-refresh stats after action buttons complete
- "Last scraped" and "last tagged" timestamps
- Events-by-day chart or mini calendar
- Quick filter shortcuts ("Lafayette only", "This weekend", "Free events")

### Weekend Page Improvements

- Map view of weekend events (Leaflet.js)
- Export to calendar (.ics download)
- Weather-based recommendations
- Time-slot planner (morning vs afternoon, avoiding nap time)

### Data & Scraping

- Configure OpenAI API key for real LLM tagging (currently all heuristic)
- Playwright-based scrapers for library sites (LibCal needs JS)
- Facebook Groups scraper (Playwright + auth)
- De-duplicate events across sources (same event on Eventbrite + AllEvents)
- Scrape event images for card thumbnails

### Notifications

- Configure a real notification channel (Telegram is easiest)
- Email digest with HTML formatting (Resend)
- "Snooze" or "Not interested" on individual events
- Notification history page

### Infrastructure

- Health check endpoint (`GET /health`)
- Request logging middleware
- Error pages (404, 500) with proper templates
- Database backup cron job
- Rate limiting on API endpoints
- `SESSION_SECRET` env var (currently uses dev fallback)
