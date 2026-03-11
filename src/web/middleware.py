"""Custom middleware for the web app."""

from __future__ import annotations

import logging
import time
from typing import cast

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.web.common import client_ip

logger = logging.getLogger("uvicorn.error")


def _is_local_http_request(request: Request) -> bool:
    host = (request.url.hostname or "").strip("[]").lower()
    return request.url.scheme == "http" and host in {"127.0.0.1", "localhost", "::1"}


def _strip_secure_from_session_cookie(response: Response) -> None:
    rewritten: list[tuple[bytes, bytes]] = []
    for name, value in response.raw_headers:
        if name.lower() != b"set-cookie" or b"session=" not in value.lower():
            rewritten.append((name, value))
            continue
        cleaned = value.replace(b"; Secure", b"").replace(b"; secure", b"")
        rewritten.append((name, cleaned))
    response.raw_headers = rewritten


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log each request with status and duration."""

    async def dispatch(self, request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "%s %s status=%s duration_ms=%.1f ip=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            client_ip(request),
        )
        return response


class LocalSessionCookieMiddleware(BaseHTTPMiddleware):
    """Allow local HTTP development to keep the session cookie usable."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if _is_local_http_request(request):
            _strip_secure_from_session_cookie(cast(Response, response))
        return response
