# Auth & Profile System — Implementation Plan

## Overview

Add user accounts (signup/login/logout), cookie-based sessions, and a profile
page with per-user interest preferences, location settings, notification
configuration, and theme preference. Link existing `sources` to users.

---

## 1. New Dependencies

Add to `pyproject.toml` `dependencies`:

```bash
"itsdangerous>=2.1",   # already required by starlette SessionMiddleware
"bcrypt>=4.0",          # password hashing
```

---

## 2. New Configuration

Add to `Settings` in `src/config.py`:

```python
# Auth
secret_key: str = "change-me-in-production"   # signs session cookies
session_cookie_name: str = "session"
session_max_age: int = 86400 * 30              # 30 days
```

---

## 3. Database Schema Changes

### 3a. New table: `users`

```sql
CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,           -- uuid
    email           TEXT NOT NULL UNIQUE,
    display_name    TEXT NOT NULL,
    password_hash   TEXT NOT NULL,
    -- location settings
    home_city       TEXT NOT NULL DEFAULT 'Lafayette',
    preferred_cities TEXT NOT NULL DEFAULT '["Lafayette", "Baton Rouge"]',  -- JSON array
    -- child interest profile (JSON)
    interest_loves  TEXT NOT NULL DEFAULT '["animals","playground","water_play","music","trains","art_messy"]',
    interest_likes  TEXT NOT NULL DEFAULT '["nature_walks","story_time","dancing"]',
    interest_dislikes TEXT NOT NULL DEFAULT '["loud_crowds","sitting_still_long","dark_spaces"]',
    -- constraints
    max_drive_time_minutes INTEGER NOT NULL DEFAULT 45,
    nap_time        TEXT NOT NULL DEFAULT '13:00-15:00',
    bedtime         TEXT NOT NULL DEFAULT '19:30',
    budget_per_event REAL NOT NULL DEFAULT 30.0,
    -- preferences
    theme           TEXT NOT NULL DEFAULT 'light' CHECK(theme IN ('light', 'dark')),
    -- notification prefs (JSON object)
    notification_settings TEXT NOT NULL DEFAULT '{"channels": ["console"], "weekend_reminder": true, "new_events": false}',
    -- timestamps
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
```

**Design rationale — flat columns vs JSON blobs:**

- `home_city`, `max_drive_time_minutes`, `nap_time`, `bedtime`, `budget_per_event`, `theme`
  → **flat columns** because we query/filter on these or display them directly.
- `preferred_cities`, `interest_loves/likes/dislikes`, `notification_settings`
  → **JSON text columns** because they're variable-length lists/objects loaded as a unit.
  SQLite's `json_extract()` is available if we ever need to query into them.

### 3b. Alter `sources` table — add `user_id`

```sql
ALTER TABLE sources ADD COLUMN user_id TEXT REFERENCES users(id);
CREATE INDEX IF NOT EXISTS idx_sources_user_id ON sources(user_id);
```

Existing rows (builtin sources) will have `user_id = NULL`, which is fine —
NULL means "system/global source". User-added sources get the creator's id.

---

## 4. Pydantic Models

Add to `src/db/models.py`:

```python
class User(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    email: str
    display_name: str
    password_hash: str = ""  # never sent to templates
    # location
    home_city: str = "Lafayette"
    preferred_cities: list[str] = Field(
        default_factory=lambda: ["Lafayette", "Baton Rouge"]
    )
    # interests
    interest_loves: list[str] = Field(
        default_factory=lambda: [
            "animals", "playground", "water_play", "music", "trains", "art_messy"
        ]
    )
    interest_likes: list[str] = Field(
        default_factory=lambda: ["nature_walks", "story_time", "dancing"]
    )
    interest_dislikes: list[str] = Field(
        default_factory=lambda: ["loud_crowds", "sitting_still_long", "dark_spaces"]
    )
    # constraints
    max_drive_time_minutes: int = 45
    nap_time: str = "13:00-15:00"
    bedtime: str = "19:30"
    budget_per_event: float = 30.0
    # preferences
    theme: Literal["light", "dark"] = "light"
    notification_settings: dict[str, Any] = Field(
        default_factory=lambda: {
            "channels": ["console"],
            "weekend_reminder": True,
            "new_events": False,
        }
    )
    # timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))

    def to_interest_profile(self) -> InterestProfile:
        """Build an InterestProfile from this user's stored preferences."""
        return InterestProfile(
            loves=self.interest_loves,
            likes=self.interest_likes,
            dislikes=self.interest_dislikes,
            constraints=Constraints(
                max_drive_time_minutes=self.max_drive_time_minutes,
                preferred_cities=self.preferred_cities,
                home_city=self.home_city,
                nap_time=self.nap_time,
                bedtime=self.bedtime,
                budget_per_event=self.budget_per_event,
            ),
        )
```

The existing `InterestProfile` and `Constraints` models stay as-is — they
become the "runtime" format built from user DB fields via `to_interest_profile()`.

---

## 5. Database Methods

Add to `Database` class in `src/db/database.py`:

```python
# ── Users ──

async def create_user(self, user: User) -> str:
    """Insert a new user. Returns the user id."""
    ...

async def get_user_by_id(self, user_id: str) -> User | None:
    ...

async def get_user_by_email(self, email: str) -> User | None:
    ...

async def update_user_profile(self, user_id: str, **fields) -> None:
    """Update arbitrary profile fields. Validates field names against an allow-list."""
    ...

async def update_user_password(self, user_id: str, password_hash: str) -> None:
    ...

async def get_sources_for_user(self, user_id: str) -> list[Source]:
    """Get sources created by a specific user."""
    ...
```

Also modify `create_source` to accept and store `user_id`, and
`_row_to_source` to handle the new column.

Add `_row_to_user` converter (parses JSON list/object columns).

---

## 6. Auth Module — `src/web/auth.py`

New file with password and session utilities:

```python
import bcrypt
from fastapi import Request, HTTPException
from starlette.responses import RedirectResponse
from functools import wraps

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

def login_user(request: Request, user_id: str) -> None:
    """Store user_id in the signed session cookie."""
    request.session["user_id"] = user_id

def logout_user(request: Request) -> None:
    request.session.clear()

async def get_current_user(request: Request, db) -> User | None:
    """Read user_id from session, load from DB. Returns None if not logged in."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return await db.get_user_by_id(user_id)

def require_login(handler):
    """Decorator for routes that require auth. Redirects to /login."""
    @wraps(handler)
    async def wrapper(request: Request, *args, **kwargs):
        user = await get_current_user(request, request.app.state.db)
        if not user:
            return RedirectResponse("/login", status_code=302)
        request.state.user = user
        return await handler(request, *args, **kwargs)
    return wrapper
```

---

## 7. Session Middleware Setup

In `src/web/app.py`, add Starlette's built-in `SessionMiddleware`:

```python
from starlette.middleware.sessions import SessionMiddleware
from src.config import settings

app = FastAPI(title="Family Events", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie=settings.session_cookie_name,
    max_age=settings.session_max_age,
    same_site="lax",
    https_only=False,  # set True in production behind HTTPS
)
```

This uses `itsdangerous` under the hood to sign/verify the cookie.
The session data (just `{"user_id": "..."}`) is stored *in* the cookie
itself (base64 + HMAC signature), so no server-side session store is needed.

---

## 8. Template Context — Inject `user` Globally

Add a middleware or modify the Jinja2 template globals so every template
has access to the current user (or `None`):

```python
# In app.py — add a middleware that loads the user for every request
@app.middleware("http")
async def inject_user(request: Request, call_next):
    request.state.user = await get_current_user(request, db)
    response = await call_next(request)
    return response
```

Then in template rendering, always pass `user=request.state.user`.
Or better, make a helper:

```python
def ctx(request: Request, **extra) -> dict:
    """Build template context with user always included."""
    return {"request": request, "user": getattr(request.state, "user", None), **extra}
```

---

## 9. Routes

### 9a. Auth Routes (new)

| Method | Path          | Auth? | Description                                    |
|--------|---------------|-------|------------------------------------------------|
| GET    | `/signup`     | No    | Render signup form                             |
| POST   | `/signup`     | No    | Create account, log in, redirect to `/`        |
| GET    | `/login`      | No    | Render login form                              |
| POST   | `/login`      | No    | Validate creds, set session, redirect to `/`   |
| POST   | `/logout`     | Yes   | Clear session, redirect to `/login`            |

### 9b. Profile Routes (new)

| Method | Path                          | Auth? | Description                                              |
|--------|-------------------------------|-------|----------------------------------------------------------|
| GET    | `/profile`                    | Yes   | Full profile page (tabbed: info, interests, location, notifications) |
| PUT    | `/api/profile/info`           | Yes   | HTMX: update display_name, email                         |
| PUT    | `/api/profile/interests`      | Yes   | HTMX: update loves/likes/dislikes lists                  |
| PUT    | `/api/profile/location`       | Yes   | HTMX: update home_city, preferred_cities, constraints    |
| PUT    | `/api/profile/notifications`  | Yes   | HTMX: update notification_settings                       |
| PUT    | `/api/profile/theme`          | Yes   | HTMX: toggle theme (returns updated header/body class)   |
| PUT    | `/api/profile/password`       | Yes   | HTMX: change password (current + new + confirm)          |

All `PUT` endpoints accept form data, validate, update DB, and return an
HTMX partial snippet confirming the change.

### 9c. Modified Existing Routes

- **`/weekend`** — use `request.state.user.to_interest_profile()` instead of
  `InterestProfile()` (fall back to defaults if not logged in).
- **`/sources`** — when logged in, only show user's own sources (plus builtins).
- **`POST /api/sources`** — set `user_id` from session when creating a source.

---

## 10. New Templates

### `login.html`

Standalone page (extends `base.html`). Centered card with email + password
fields. Link to `/signup`. Form POSTs normally (not HTMX) for
the redirect. Error message shown inline if login fails (`error` context var).

### `signup.html`

Same layout as login. Fields: display name, email, password, confirm password.
POSTs normally. Redirects to `/` on success.

### `profile.html`

Extends `base.html`. Uses HTMX tabs (or accordion sections) for:

1. **Account Info** — display name, email, change password button
2. **Child Interests** — tag-picker UI for loves/likes/dislikes
   (predefined options + custom text input)
3. **Location & Schedule** — home city dropdown, preferred cities checkboxes,
   nap time, bedtime, budget, max drive time
4. **Notifications** — channel checkboxes (console, email, sms, telegram),
   weekend reminder toggle, new events toggle
5. **My Sources** — list of sources this user added (links to source detail)
6. **Theme** — light/dark toggle

Each section has its own `<form>` that PUTs via HTMX to the corresponding
`/api/profile/*` endpoint, with inline success feedback.

### Partials

- `partials/_profile_info.html`
- `partials/_profile_interests.html`
- `partials/_profile_location.html`
- `partials/_profile_notifications.html`
- `partials/_profile_sources.html`

### Modified: `base.html`

Nav bar changes:

- If logged in: show user display name + link to `/profile` + logout button
- If not logged in: show "Login" link

---

## 11. Implementation Order

Phased approach — each phase is independently shippable:

### Phase 1: Auth Infrastructure (no visible profile yet)

1. Add `itsdangerous` and `bcrypt` to `pyproject.toml`, `pip install`
2. Add `secret_key` etc. to `Settings`
3. Add `users` table SQL to `database.py` `connect()` method
4. Add `User` model to `models.py`
5. Add `_row_to_user`, `create_user`, `get_user_by_id`, `get_user_by_email` to `database.py`
6. Create `src/web/auth.py` (hash/verify, session helpers, `get_current_user`, `require_login`)
7. Add `SessionMiddleware` to `app.py`
8. Add `inject_user` middleware to `app.py`
9. Create `login.html` and `signup.html` templates
10. Add `GET/POST /signup`, `GET/POST /login`, `POST /logout` routes
11. Update `base.html` nav to show login/profile/logout conditionally
12. **Test**: signup → login → see nav change → logout → redirected

### Phase 2: Profile Page — Basic Info & Interests

1. Add `update_user_profile` to `database.py`
2. Create `profile.html` template with tabbed sections
3. Create `partials/_profile_info.html` and `partials/_profile_interests.html`
4. Add `GET /profile`, `PUT /api/profile/info`, `PUT /api/profile/interests` routes
5. **Test**: edit display name, edit interest tags, see them persist

### Phase 3: Location, Constraints, Theme

1. Create `partials/_profile_location.html`
2. Add `PUT /api/profile/location` route
3. Add `PUT /api/profile/theme` route
4. Wire dark mode via a CSS class on `<html>` based on `user.theme`
5. Modify `/weekend` to use logged-in user's `InterestProfile`
6. **Test**: change home city → weekend rankings change

### Phase 4: Sources Ownership & Notifications

1. Run `ALTER TABLE sources ADD COLUMN user_id` migration
2. Add `get_sources_for_user` to `database.py`
3. Modify `POST /api/sources` to set `user_id`
4. Create `partials/_profile_sources.html` (lists user's sources on profile)
5. Create `partials/_profile_notifications.html`
6. Add `PUT /api/profile/notifications` route
7. Modify notification dispatcher to read per-user settings
8. **Test**: add source as logged-in user → appears on profile

### Phase 5: Password Change & Polish

1. Add `PUT /api/profile/password` route
2. Add form validation feedback (HTMX inline errors)
3. Add "are you sure" confirmation on destructive actions
4. Graceful degradation: app still works if not logged in (uses defaults)

---

## 12. Route Signatures (Exact)

```python
# ── Auth ──

@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    ...

@app.post("/signup", response_class=HTMLResponse)
async def signup_submit(request: Request):
    form = await request.form()
    # fields: email, display_name, password, password_confirm
    ...

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    ...

@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request):
    form = await request.form()
    # fields: email, password
    ...

@app.post("/logout")
async def logout(request: Request):
    ...

# ── Profile ──

@app.get("/profile", response_class=HTMLResponse)
@require_login
async def profile_page(request: Request):
    ...

@app.put("/api/profile/info", response_class=HTMLResponse)
@require_login
async def update_profile_info(request: Request):
    form = await request.form()
    # fields: display_name, email
    ...

@app.put("/api/profile/interests", response_class=HTMLResponse)
@require_login
async def update_profile_interests(request: Request):
    form = await request.form()
    # fields: loves (comma-sep or multi-select), likes, dislikes
    ...

@app.put("/api/profile/location", response_class=HTMLResponse)
@require_login
async def update_profile_location(request: Request):
    form = await request.form()
    # fields: home_city, preferred_cities, max_drive_time_minutes,
    #         nap_time, bedtime, budget_per_event
    ...

@app.put("/api/profile/notifications", response_class=HTMLResponse)
@require_login
async def update_profile_notifications(request: Request):
    form = await request.form()
    # fields: channels (multi-checkbox), weekend_reminder, new_events
    ...

@app.put("/api/profile/theme", response_class=HTMLResponse)
@require_login
async def update_profile_theme(request: Request):
    form = await request.form()
    # fields: theme ("light" | "dark")
    ...

@app.put("/api/profile/password", response_class=HTMLResponse)
@require_login
async def update_profile_password(request: Request):
    form = await request.form()
    # fields: current_password, new_password, confirm_password
    ...
```

---

## 13. DB Migration Strategy

Since this is early-stage with SQLite, use the **idempotent DDL in `connect()`**
pattern already established:

```python
async def connect(self) -> None:
    ...
    # Existing tables
    await self._db.execute(_CREATE_EVENTS_TABLE)
    await self._db.execute(_CREATE_SOURCES_TABLE)
    # New tables
    await self._db.execute(_CREATE_USERS_TABLE)
    # Migrations (safe to run multiple times)
    await self._maybe_add_column("sources", "user_id", "TEXT REFERENCES users(id)")
    ...

async def _maybe_add_column(self, table: str, column: str, definition: str) -> None:
    """Add a column if it doesn't exist (SQLite has no IF NOT EXISTS for ALTER)."""
    async with self.db.execute(f"PRAGMA table_info({table})") as cursor:
        columns = [row[1] for row in await cursor.fetchall()]
    if column not in columns:
        await self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
```

---

## 14. Security Notes

- **bcrypt** with default cost factor (12 rounds) for password hashing
- **Signed cookie session** via itsdangerous HMAC — tamper-proof, not encrypted
  (session only holds `user_id`, no secrets)
- **CSRF**: Starlette SessionMiddleware sets `SameSite=lax` by default, which
  blocks cross-origin POST. HTMX requests include the session cookie
  automatically. This is sufficient for now; add CSRF tokens later if needed.
- **No rate limiting** on login for now (single-family app). Add later if exposed
  to internet.
- **`secret_key`** should be overridden via env var in production.

---

## 15. Graceful Degradation

The app must continue to work for anonymous users:

- `/`, `/events`, `/weekend` — accessible without login
- `/weekend` — uses `InterestProfile()` defaults if no user logged in
- `/sources` — shows all sources (no user filtering) if anonymous
- `/profile`, `/api/profile/*` — redirect to `/login`
