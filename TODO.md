# TODO: Family Events

## Completed

### ✅ Refactor Frontend to Jinja2 + HTMX + Tailwind CSS

- 20 Jinja2 templates (10 pages + 10 partials)
- `base.html` shared layout with sticky header, tab nav, toast JS
- `app.py` is pure route handlers (~650 lines)
- All API endpoints return toast or HTML snippets for HTMX

### ✅ Events Page: Pagination, Search, Filtering

- SQL-level LIKE search, filter by city/source/tagged/score, sort
- 300ms debounced search via HTMX `keyup changed delay:300ms`
- Server-side pagination (25/page) with `hx-push-url` for bookmarkable URLs
- HTMX partial rendering: `HX-Request` header → returns only card grid + pagination
- 4-column responsive card grid with images, score badges, category tags

### ✅ Loading Skeleton Animations

- Global 3px indeterminate progress bar (brand color)
- Events: skeleton card grid overlay during loads
- Action buttons: CSS spinner (`spinner` / `spinner-brand`) + disabled state

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

- Tailwind v4 `@custom-variant dark` with per-user theme preference
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

- Hamburger menu on <md screens with search + stacked nav links
- Events: single-column card grid on mobile, 2-col sm, 3-col lg, 4-col xl
- Discover: horizontal scroll cards adapt width for mobile
- Filter dropdowns wrap responsively
- Pagination: 40px min tap targets, ellipsis for distant pages
- Toast container: bottom on mobile, top-right desktop
- Tested at 390px viewport width

### ✅ Animations & Micro-interactions

- 5 custom Tailwind animations (fade-in, fade-in-up, slide-down, scale-in, pop-in)
- Staggered card/section entrances (stagger-1–9, 50ms increments)
- Hover: card lift + shadow-card-hover, image zoom (scale-105), title color → brand
- Button press: active:scale-[0.98]
- HTMX swap fade transitions (.htmx-swapping/.htmx-settling)
- `prefers-reduced-motion` disables all animations

### ✅ Tailwind CSS v4 Build

- Tailwind CSS 4.2 via `@tailwindcss/cli`, build with `npm run css:build`
- CSS-based config in `input.css` via `@theme` (design tokens) + `@custom-variant` (dark mode)
- No JS config file — v4 auto-detects template content
- ~46KB minified output

### ✅ Complete UI Redesign — Eventbrite-inspired

- **Design system:** Custom CSS variables for brand coral (#F05537), surfaces, text, borders, shadows
- **Dark mode:** CSS variable overrides on `.dark` class (slate palette), quick-toggle in header
- **Score visualization:** 4-tier color system (gray/amber/emerald/green) for toddler scores 0-10
- **Layout:** Sticky header with search, tab nav (Discover/Browse/This Weekend/Sources/Admin), user dropdown, footer
- **Discover page (/):** Horizontal scroll card sections by category (Top Picks, Arts, Outdoor, Nature), admin quick actions
- **Browse (/events):** 4-column responsive card grid with images, score badges, free badges, category tags
- **Weekend (/weekend):** Weather banner (gradient), ranked compact cards with thumbnails + medals + points
- **Event Detail (/event/{id}):** Hero image, info card with large score badge, AI tags 3-column grid with icons
- **Sources (/sources):** Built-in source cards with icons, add custom source form, status badges
- **Settings (/profile):** Organized sections (appearance, location, child prefs, notifications, password)
- **Auth (/login, /signup):** Centered card layout with balloon branding
- **Mobile:** Hamburger menu with search, single-column card layouts, touch-friendly pagination
- `active_page` context variable for nav tab highlighting
- Category event lists (arts, outdoor, nature) computed server-side for discover page
- Max-width 7xl container (was 4xl)
- Image placeholders with category emojis for events without images

---

## Up Next

### Event Detail Improvements

- Collapsible raw data section
- Google Maps link for event location
- "Share this event" button (copy link)
- Related/similar events at bottom
- Scoring breakdown (why this score?)

### Dashboard / Discover Improvements

- Auto-refresh stats after action buttons complete
- "Last scraped" and "last tagged" timestamps
- Events-by-day chart or mini calendar
- More category sections (Music, Sports, Free Events)
- "Near you" section based on user's home city

### Weekend Page Improvements

- Map view of weekend events (Leaflet.js)
- Export to calendar (.ics download)
- Weather-based recommendations (rain → indoor, heat → water/shade)
- Time-slot planner (morning vs afternoon, avoiding nap time)
- Saturday / Sunday column split view

### Data & Scraping

- Configure OpenAI API key for real LLM tagging (currently all heuristic)
- Playwright-based scrapers for library sites (LibCal needs JS)
- Facebook Groups scraper (Playwright + auth)
- De-duplicate events across sources (same event on Eventbrite + AllEvents)
- Improve image coverage (384 of 767 events missing images)

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
