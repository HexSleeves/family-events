# TODO: Refactor Frontend to Jinja2 + HTMX + Tailwind CSS

## Goal

Replace the inline HTML-in-Python-f-strings in `src/web/app.py` (358 lines) with
proper Jinja2 templates, HTMX for interactivity, and Tailwind CSS for styling.
No npm, no bundler, no SPA framework.

## Why

- Current HTML is embedded in Python f-strings - impossible to syntax-highlight or edit
- No template inheritance (the `_page()` wrapper is fragile)
- Action buttons use inline `onclick` with raw `fetch()` calls
- Styling is a single inline `<style>` block duplicated on every page

## Stack

- **Jinja2** - already in dependencies, already imported by FastAPI
- **HTMX** - single CDN script tag, zero build step. Use for all button actions and partial page updates
- **Tailwind CSS** - use the CDN play script (`<script src="https://cdn.tailwindcss.com"></script>`) for development. No build step needed. For production, optionally switch to the Tailwind CLI standalone binary to generate a minified CSS file

## File Structure to Create

```
src/web/
  templates/
    base.html              # Shared layout: <html>, <head>, nav, Tailwind CDN, HTMX CDN
    dashboard.html         # Stats cards, action buttons, top events list
    events.html            # Events table with all columns
    event_detail.html      # Single event: info card, AI tags grid, raw data
    weekend.html           # Ranked picks, weather, notification preview
    partials/
      _event_card.html     # Reusable event card (used in dashboard + weekend)
      _event_row.html      # Table row (used in events page)
      _tags_grid.html      # AI tags 2-column grid (used in event detail)
      _stats.html          # Stats bar partial (for HTMX refresh)
      _notification.html   # Notification preview block
  static/
    (empty for now - Tailwind via CDN, HTMX via CDN)
  app.py                   # Route handlers only - no HTML strings
```

## Step-by-Step Plan

### 1. Set up Jinja2 with FastAPI

```python
# src/web/app.py
from fastapi.templating import Jinja2Templates
from pathlib import Path

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
```

### 2. Create base.html

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{% block title %}Family Events{% endblock %}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/htmx.org@2.0.4"></script>
</head>
<body class="bg-gray-50 text-gray-900">
    <header class="bg-gradient-to-r from-indigo-500 to-purple-500 text-white py-4 mb-6">
        <div class="max-w-4xl mx-auto px-4 flex items-center">
            <h1 class="text-xl font-bold">🌟 Family Events</h1>
            <nav class="ml-auto flex gap-2">
                <a href="/" class="px-3 py-1.5 rounded-md bg-white/15 hover:bg-white/30 text-sm">🏠 Dashboard</a>
                <a href="/events" class="px-3 py-1.5 rounded-md bg-white/15 hover:bg-white/30 text-sm">📅 Events</a>
                <a href="/weekend" class="px-3 py-1.5 rounded-md bg-white/15 hover:bg-white/30 text-sm">🎉 Weekend</a>
            </nav>
        </div>
    </header>
    <main class="max-w-4xl mx-auto px-4 pb-12">
        {% block content %}{% endblock %}
    </main>
    <!-- HTMX loading indicator -->
    <div id="toast" class="fixed bottom-4 right-4 hidden"></div>
</body>
</html>
```

### 3. Convert each page

For each route in app.py, extract the HTML into a template and replace with:

```python
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    events = await db.get_recent_events(days=30)
    # ... compute stats, top_events ...
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "total": total,
        "tagged": tagged,
        "untagged": untagged,
        "sources": sources,
        "top_events": top_events,
    })
```

### 4. HTMX for action buttons

Replace the inline onclick/fetch JS with HTMX attributes:

```html
<!-- Before (inline JS) -->
<button onclick="fetch('/api/scrape',{method:'POST'}).then(...)">Run Scrapers</button>

<!-- After (HTMX) -->
<button
    hx-post="/api/scrape"
    hx-target="#action-status"
    hx-swap="innerHTML"
    hx-indicator="#spinner"
    class="bg-indigo-500 hover:bg-indigo-600 text-white px-4 py-2 rounded-lg font-semibold text-sm"
>
    🔄 Run Scrapers
</button>
<span id="spinner" class="htmx-indicator">Loading...</span>
<div id="action-status"></div>
```

Add new API endpoints that return HTML partials (not JSON) for HTMX:

```python
@app.post("/api/scrape", response_class=HTMLResponse)
async def api_scrape(request: Request):
    count = await run_scrape(db)
    # Return an HTML snippet, not JSON
    return f'<div class="text-green-600 font-semibold">Scraped {count} events ✅</div>'
```

Or better yet, return a partial template. Keep the existing JSON API endpoints
too (rename to `/api/v1/...`) so they stay usable by the CLI and notifications.

### 5. HTMX for "Mark Attended"

```html
<button
    hx-post="/api/attend/{{ event.id }}"
    hx-swap="outerHTML"
    class="..."
>
    ✅ Mark Attended
</button>
```

After click, swap the button with a "Attended ✅" label returned from the server.

### 6. Tailwind CSS Notes

- Use the **Tailwind CDN play script** (`<script src="https://cdn.tailwindcss.com"></script>`) in base.html. This is the simplest approach - no build step, works immediately.
- The CDN script is ~115KB and compiles Tailwind classes in the browser at runtime. Fine for a personal single-user tool.
- **Do NOT use an inline `<style>` block for layout.** Use Tailwind utility classes directly on elements.
- Carry over the current design language: indigo/purple gradient header, white cards with subtle shadows, green/orange/gray badges.
- Key Tailwind classes mapping from current CSS:
  - `.card` → `bg-white rounded-xl p-5 mb-4 shadow-sm`
  - `.badge` → `inline-block px-2 py-0.5 rounded-full text-xs font-semibold`
  - `.badge-green` → `bg-green-100 text-green-800`
  - `.badge-orange` → `bg-orange-100 text-orange-800`
  - `.badge-gray` → `bg-gray-100 text-gray-700`
  - `.stat` → `bg-white rounded-xl p-4 shadow-sm text-center flex-1 min-w-[120px]`
  - `.score` → `text-3xl font-bold text-indigo-500`
  - `.btn-primary` → `bg-indigo-500 hover:bg-indigo-600 text-white px-4 py-2 rounded-lg font-semibold text-sm`
- If you later want a production build (smaller CSS), install the Tailwind CLI standalone binary and run: `tailwindcss -i input.css -o static/styles.css --minify`

### 7. Preserve existing behavior

- All 4 pages must render identically (dashboard, events, event detail, weekend)
- All 5 API endpoints must keep working (scrape, tag, notify, attend, events list)
- The `_page()` helper and all inline HTML in app.py should be deleted entirely
- Keep `format_console_message()` in formatter.py unchanged (used by notifications)

## Reference: Current Page Data Requirements

### Dashboard (`GET /`)
- `total`, `tagged`, `untagged` (event counts)
- `sources` (unique source count)
- `top_events` (list of Event, sorted by toddler_score, limit 5)

### Events (`GET /events`)
- `events` (list of Event, sorted by start_time)
- Each row: date, time, title+link, city, source badge, score badge, categories, view link

### Event Detail (`GET /event/{id}`)
- `event` (single Event with all fields)
- Full EventTags grid if tagged
- Raw JSON data (collapsible)

### Weekend (`GET /weekend`)
- `saturday`, `sunday` (date objects)
- `weather` dict with `saturday`/`sunday` DayForecast
- `ranked` (list of (Event, score) tuples, limit 10)
- `message` (notification preview string from formatter)

## Quality Checks

After refactoring, run:
```bash
ruff format src/
ruff check src/
ty check
```
All must pass clean.

## Don't Forget

- `from fastapi import Request` is needed for Jinja2 templates
- Jinja2 auto-escapes HTML by default - use `{{ value }}` not `{{ value | safe }}` unless intentional
- HTMX needs `hx-` attributes, no special server config
- The `templates` directory path should be relative to `app.py` using `Path(__file__).parent / "templates"`
- Tailwind CDN script goes in `<head>` of base.html, nothing else needed
