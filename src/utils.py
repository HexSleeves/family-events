"""Shared utility helpers used across pipeline, scheduler, cron, and tagger modules."""

from __future__ import annotations

import logging
import time

logger = logging.getLogger("uvicorn.error")


def duration_ms(started: float) -> float:
    """Milliseconds elapsed since *started* (a ``time.perf_counter()`` value)."""
    return round((time.perf_counter() - started) * 1000, 2)


def runtime_log(level: int, event: str, **context: object) -> None:
    """Structured log helper — drops ``None`` values from *context*."""
    logger.log(
        level,
        event,
        extra={k: v for k, v in context.items() if v is not None},
    )


def error_details(exc: BaseException) -> tuple[str, str]:
    """Return ``(type_name, message)`` for an exception."""
    message = str(exc).strip() or repr(exc)
    return type(exc).__name__, message
