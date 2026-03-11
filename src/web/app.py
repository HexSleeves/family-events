"""FastAPI web admin for Family Events."""

from __future__ import annotations

from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from src.config import settings
from src.db.database import Database, create_database
from src.web.common import ctx, template_response
from src.web.jobs import job_registry
from src.web.middleware import LocalSessionCookieMiddleware, RequestLoggingMiddleware
from src.web.routes.auth import router as auth_router
from src.web.routes.calendar import router as calendar_router
from src.web.routes.events import router as events_router
from src.web.routes.jobs import router as jobs_router
from src.web.routes.pages import router as pages_router
from src.web.routes.pipeline import router as pipeline_router
from src.web.routes.profile import router as profile_router
from src.web.routes.sources import router as sources_router

db = create_database()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

_rate_limit_store: dict[str, deque[float]] = {}
_bulk_unattend_undo_store: dict[str, list[str]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await cast(Database, app.state.db).connect()
    await job_registry.recover_stale_jobs()
    yield
    await cast(Database, app.state.db).close()


app = FastAPI(title="Family Events", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.add_middleware(cast(Any, RequestLoggingMiddleware))
if not settings.session_secret:
    raise RuntimeError("SESSION_SECRET must be set (in .env) before starting the web app")

app.add_middleware(
    cast(Any, SessionMiddleware),
    secret_key=settings.session_secret,
    session_cookie="session",
    max_age=max(300, settings.session_max_age_seconds),
    same_site=settings.session_cookie_same_site,
    https_only=settings.session_cookie_secure,
    domain=settings.session_cookie_domain or None,
)
app.add_middleware(cast(Any, LocalSessionCookieMiddleware))
app.state.db = db
app.state.templates = templates
app.state.rate_limit_store = _rate_limit_store
app.state.bulk_unattend_undo_store = _bulk_unattend_undo_store

app.include_router(auth_router)
app.include_router(profile_router)
app.include_router(sources_router)
app.include_router(jobs_router)
app.include_router(calendar_router)
app.include_router(events_router)
app.include_router(pipeline_router)
app.include_router(pages_router)


@app.exception_handler(404)
async def not_found_handler(request: Request, _exc: Exception) -> HTMLResponse:
    """Render friendly 404 page for missing routes."""
    return template_response(request, "404.html", await ctx(request), status_code=404)


@app.exception_handler(500)
async def server_error_handler(request: Request, _exc: Exception) -> HTMLResponse:
    """Render friendly 500 page for unhandled server errors."""
    return template_response(request, "500.html", await ctx(request), status_code=500)
