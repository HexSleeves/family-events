"""Custom middleware for the web app."""

from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware

from src.web.common import client_ip

logger = logging.getLogger("uvicorn.error")


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
