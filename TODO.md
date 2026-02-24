# TODO: Family Events

## Completed

### ✅ Refactor Frontend to Jinja2 + HTMX + Tailwind CSS

- 20 Jinja2 templates (10 pages + 10 partials)
- `base.html` shared layout with nav, HTMX CDN
- `app.py` is pure route handlers (629 lines)
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
  - Appearance (light/dark/system theme) with disabled-when-unchanged Save button
  - Location (home city, preferred cities)
  - Child preferences (loves, likes, dislikes, constraints)
  - Notification channel selection
  - User's sources list
  - Password change
- Nav bar adapts to auth state (login/signup vs user/logout)

### ✅ Dark Mode

- Tailwind `darkMode: 'class'` with per-user theme preference
- Auto mode respects `prefers-color-scheme` with live OS change listener
- Inline `<head>` script prevents FOUC for auto theme
- All 20 templates have `dark:` variant classes

### ✅ Toast Notifications

- Replaced all inline success/error HTML with toast system
- `_toast()` server helper returns `HX-Trigger` header
- Single `htmx:afterRequest` handler (no duplicate event listeners)
- 4 variants: success (green), error (red), warning (amber), info (blue)
- Slide-in animation, 3.5s auto-dismiss, click to dismiss
- Theme changes trigger both toast + theme swap in one HX-Trigger payload

### ✅ Per-User Settings

- Notification channels, email_to, child_name stored per-user
- Interest profile (loves/likes/dislikes/constraints) per-user
- Weekend ranking + notifications use logged-in user's profile
- .env retains only secrets and infrastructure config

### ✅ Mobile Responsive Layout

- Hamburger menu on <md screens with stacked nav links
- Events table → card layout on mobile with score badge
- Filter row: 2-column grid on mobile
- Pagination: 44px min tap targets, fewer page numbers
- Dashboard buttons, source form, profile grids all stack on mobile
- Toast container: bottom-center on mobile, top-right on desktop
- Tested at 375px viewport width

### ✅ Animations & Micro-interactions

- 5 custom Tailwind animations (fade-in, fade-in-up, slide-down, scale-in, pop-in)
- Staggered card/section entrances (stagger-1–9, 60ms increments)
- Hover lift on cards/sections, active:scale-95 press on buttons
- Badge hover:scale-105 pop, table row transition-colors
- HTMX swap fade transitions (.htmx-swapping/.htmx-settling)
- `prefers-reduced-motion` disables all animations

### ✅ Tailwind Production Build

- Tailwind CSS 3.4 via npm, CLI build with `npm run css:build`
- `src/web/static/input.css` → `src/web/static/styles.css` (~26KB minified)
- `tailwind.config.js` with content paths + custom animation keyframes
- FastAPI `StaticFiles` mount at `/static`
- No CDN script tag — no console warnings
- `npm run css:watch` for development

---

## Up Next

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

### Auth & Access Control

- Require login for source management
- "My Sources" filtered view on profile
- Public vs private events
- Admin role
- Email verification
- Forgot password flow

### Infrastructure

- Health check endpoint (`GET /health`)
- Request logging middleware
- Error pages (404, 500) with proper templates
- Database backup cron job
- Rate limiting on API endpoints
- `SESSION_SECRET` env var (currently uses dev fallback)
