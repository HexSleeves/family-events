"""Shared outbound HTTP client helpers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import httpx

from src.config import settings

logger = logging.getLogger("uvicorn.error")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
RETRYABLE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})


class LoggedRetryTransport(httpx.AsyncBaseTransport):
    """Transport wrapper that adds retries plus consistent logging context."""

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport,
        *,
        service: str,
        max_retries: int,
        backoff_seconds: float,
    ) -> None:
        self._transport = transport
        self._service = service
        self._max_retries = max(0, max_retries)
        self._backoff_seconds = max(0.0, backoff_seconds)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._transport.handle_async_request(request)
                if (
                    request.method.upper() in RETRYABLE_METHODS
                    and response.status_code in RETRYABLE_STATUS_CODES
                    and attempt < self._max_retries
                ):
                    logger.warning(
                        "external_http_retry service=%s method=%s url=%s status=%s attempt=%s/%s",
                        self._service,
                        request.method,
                        request.url,
                        response.status_code,
                        attempt + 1,
                        self._max_retries + 1,
                    )
                    await response.aclose()
                    await self._sleep(attempt)
                    continue
                return response
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                last_error = exc
                if request.method.upper() not in RETRYABLE_METHODS or attempt >= self._max_retries:
                    break
                logger.warning(
                    "external_http_retry service=%s method=%s url=%s error=%s attempt=%s/%s",
                    self._service,
                    request.method,
                    request.url,
                    exc.__class__.__name__,
                    attempt + 1,
                    self._max_retries + 1,
                )
                await self._sleep(attempt)
        assert last_error is not None
        raise last_error

    async def _sleep(self, attempt: int) -> None:
        delay = self._backoff_seconds * (2**attempt)
        if delay > 0:
            await asyncio.sleep(delay)

    async def aclose(self) -> None:
        await self._transport.aclose()


def default_timeout() -> httpx.Timeout:
    return httpx.Timeout(
        settings.external_http_timeout_seconds,
        connect=settings.external_http_connect_timeout_seconds,
    )


def build_async_client(
    *,
    service: str,
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    follow_redirects: bool = True,
    max_retries: int | None = None,
    backoff_seconds: float | None = None,
    transport_factory: Callable[[], httpx.AsyncBaseTransport] | None = None,
    **kwargs: Any,
) -> httpx.AsyncClient:
    merged_headers = dict(DEFAULT_HEADERS)
    if headers:
        merged_headers.update(headers)
    transport = LoggedRetryTransport(
        transport_factory() if transport_factory else httpx.AsyncHTTPTransport(retries=0),
        service=service,
        max_retries=settings.external_http_max_retries if max_retries is None else max_retries,
        backoff_seconds=(
            settings.external_http_retry_backoff_seconds
            if backoff_seconds is None
            else backoff_seconds
        ),
    )
    return httpx.AsyncClient(
        headers=merged_headers,
        timeout=timeout or default_timeout(),
        follow_redirects=follow_redirects,
        transport=transport,
        **kwargs,
    )


async def close_response(response: httpx.Response | None) -> None:
    if response is not None:
        await response.aclose()
