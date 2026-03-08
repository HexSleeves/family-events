"""Shared web helpers for responses, auth checks, and request state."""

from __future__ import annotations

import json
import logging
import secrets
import time
from collections import deque
from datetime import UTC, datetime
from typing import cast

from fastapi import Request
from fastapi.datastructures import FormData
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.config import settings
from src.db.database import Database
from src.db.models import User
from src.scrapers.analyzer import validate_public_http_url
from src.web.auth import ensure_csrf_token, get_current_user

logger = logging.getLogger("uvicorn.error")

RateLimitStore = dict[str, deque[float]]
BulkUndoStore = dict[str, list[str]]


def get_db(request: Request) -> Database:
    """Return the configured application database."""
    return cast(Database, request.app.state.db)


def get_templates(request: Request) -> Jinja2Templates:
    """Return the configured template renderer."""
    return cast(Jinja2Templates, request.app.state.templates)


def get_rate_limit_store(request: Request) -> RateLimitStore:
    """Return the in-memory rate limit store."""
    return cast(RateLimitStore, request.app.state.rate_limit_store)


def get_bulk_unattend_undo_store(request: Request) -> BulkUndoStore:
    """Return the in-memory bulk unattend undo store."""
    return cast(BulkUndoStore, request.app.state.bulk_unattend_undo_store)


def _merge_hx_trigger(headers: dict[str, str] | None, payload: dict[str, object]) -> dict[str, str]:
    merged = dict(headers or {})
    existing = merged.get("HX-Trigger")
    if existing:
        current = json.loads(existing)
        if not isinstance(current, dict):
            raise ValueError("HX-Trigger header must be a JSON object")
    else:
        current = {}
    current.update(payload)
    merged["HX-Trigger"] = json.dumps(current)
    return merged


def is_htmx_request(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def hx_target(request: Request) -> str:
    return request.headers.get("HX-Target", "")


def htmx_redirect_or_redirect(request: Request, location: str, status_code: int = 302):
    if is_htmx_request(request):
        return HTMLResponse("", status_code=200, headers={"HX-Redirect": location})
    return RedirectResponse(location, status_code=status_code)


def template_response(
    request: Request,
    template_name: str,
    context: dict[str, object],
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> HTMLResponse:
    response = get_templates(request).TemplateResponse(template_name, context, status_code=status_code)
    for key, value in (headers or {}).items():
        response.headers[key] = value
    return response


def toast(
    message: str,
    variant: str = "success",
    *,
    status_code: int = 200,
    body: str = "",
    headers: dict[str, str] | None = None,
) -> HTMLResponse:
    """Return an HTML response that triggers a toast notification via HTMX."""
    merged_headers = _merge_hx_trigger(
        headers,
        {"showToast": {"message": message, "variant": variant}},
    )
    return HTMLResponse(content=body, status_code=status_code, headers=merged_headers)


def change_theme(theme: str, *, body: str = "", headers: dict[str, str] | None = None) -> HTMLResponse:
    """Return an HTML response that triggers a theme change plus toast."""
    label = {"light": "Light", "dark": "Dark", "auto": "System"}.get(theme, theme)
    merged_headers = _merge_hx_trigger(
        headers,
        {
            "changeTheme": {"theme": theme},
            "showToast": {"message": f"Theme set to {label}", "variant": "success"},
        },
    )
    return HTMLResponse(content=body, status_code=200, headers=merged_headers)


def null_response() -> HTMLResponse:
    """Return an empty no-op HTMX response."""
    return HTMLResponse(content="", status_code=204, headers={"HX-Trigger": json.dumps({})})


def require_login(user: User | None) -> HTMLResponse | None:
    """Require an authenticated user for API routes."""
    if user:
        return None
    return toast("Please log in first", "error", status_code=401)


def client_ip(request: Request) -> str:
    """Return best-effort client IP, honoring one trusted forwarded header value."""
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    if forwarded:
        return forwarded
    return request.client.host if request.client else "unknown"


def rate_limit_key(request: Request, route: str) -> str:
    """Build a per-IP, per-route rate limit key."""
    return f"{client_ip(request)}:{route}"


def check_rate_limit(
    request: Request,
    route: str,
    *,
    limit: int | None = None,
    window: int | None = None,
    message: str = "Too many requests. Try again in a moment.",
) -> HTMLResponse | None:
    """Simple sliding-window in-memory rate limiter."""
    limit = max(1, limit or settings.rate_limit_max_requests)
    window = max(1, window or settings.rate_limit_window_seconds)

    now = time.monotonic()
    key = rate_limit_key(request, route)
    hits = get_rate_limit_store(request).setdefault(key, deque())

    cutoff = now - window
    while hits and hits[0] < cutoff:
        hits.popleft()

    if len(hits) >= limit:
        return toast(message, "warning", status_code=429)

    hits.append(now)
    return None


def format_ts(ts: datetime | None) -> str | None:
    """Format UTC timestamps for health payloads."""
    if not ts:
        return None
    return ts.astimezone(UTC).isoformat().replace("+00:00", "Z")


def expected_origin(request: Request) -> str:
    """Return the expected origin for same-origin checks."""
    base_url = settings.app_base_url.strip().rstrip("/") or str(request.base_url).rstrip("/")
    return base_url


def require_safe_origin(request: Request) -> HTMLResponse | None:
    """Block unsafe cross-origin mutations."""
    expected = expected_origin(request)
    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if not value:
            continue
        if value.startswith(expected):
            return None
        return toast("Request blocked by origin policy", "error", status_code=403)
    return None


async def require_csrf(request: Request) -> tuple[FormData | None, HTMLResponse | None]:
    """Return parsed form data plus any CSRF denial response."""
    form = await request.form()
    if denied := require_safe_origin(request):
        return form, denied
    expected = request.session.get("csrf_token")
    provided = (
        request.headers.get("X-CSRF-Token", "").strip() or str(form.get("csrf_token", "")).strip()
    )
    if not expected or not provided or not secrets.compare_digest(str(expected), provided):
        return form, toast(
            "Security check failed. Refresh the page and try again.",
            "error",
            status_code=403,
        )
    return form, None


async def require_login_and_csrf(
    request: Request,
) -> tuple[User | None, FormData | None, HTMLResponse | None]:
    """Return current user, parsed form, plus any auth or CSRF denial response."""
    user = await get_current_user(request, get_db(request))
    if denied := require_login(user):
        return user, None, denied
    form, denied = await require_csrf(request)
    return user, form, denied


async def ctx(request: Request, **extra: object) -> dict[str, object]:
    """Build base template context with current user and CSRF token."""
    user = await get_current_user(request, get_db(request))
    csrf_token = ensure_csrf_token(request)
    return {
        "request": request,
        "current_user": user,
        "csrf_token": csrf_token,
        "active_page": extra.pop("active_page", ""),
        **extra,
    }


def validate_source_url(url: str) -> str | None:
    """Return an error message if a source URL is unsafe or invalid."""
    if len(url) > 2048:
        return "URL is too long"
    try:
        validate_public_http_url(url)
    except ValueError as exc:
        return str(exc)
    return None
