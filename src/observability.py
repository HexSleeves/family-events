from __future__ import annotations

import json
import logging
import logging.config
from datetime import UTC, date, datetime
from typing import Any, Literal

StructuredLogFormat = Literal["auto", "pretty", "json"]
ResolvedLogFormat = Literal["pretty", "json"]

_DEFAULT_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


def resolve_log_format(configured_format: str, app_env: str) -> ResolvedLogFormat:
    normalized_format = (configured_format or "auto").strip().lower()
    normalized_env = (app_env or "development").strip().lower()

    if normalized_format == "json":
        return "json"
    if normalized_format == "pretty":
        return "pretty"
    return "json" if normalized_env == "production" else "pretty"


def build_logging_config(*, app_env: str, log_format: str, log_level: str) -> dict[str, Any]:
    resolved_format = resolve_log_format(log_format, app_env)
    formatter_name = "json" if resolved_format == "json" else "pretty"
    normalized_level = (log_level or "INFO").strip().upper()

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "pretty": {"()": "src.observability.PrettyFormatter"},
            "json": {"()": "src.observability.JsonFormatter"},
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": formatter_name,
            }
        },
        "root": {"handlers": ["default"], "level": normalized_level},
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": normalized_level, "propagate": False},
            "uvicorn.error": {
                "handlers": ["default"],
                "level": normalized_level,
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["default"],
                "level": normalized_level,
                "propagate": False,
            },
        },
    }


def configure_logging(*, app_env: str, log_format: str, log_level: str) -> ResolvedLogFormat:
    resolved_format = resolve_log_format(log_format, app_env)
    logging.config.dictConfig(
        build_logging_config(app_env=app_env, log_format=resolved_format, log_level=log_level)
    )
    return resolved_format


def _extract_extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key, value in record.__dict__.items():
        if key in _DEFAULT_RECORD_FIELDS or key.startswith("_"):
            continue
        fields[key] = value
    return fields


def _serialize_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_serialize_value(item) for item in value]
    try:
        json.dumps(value)
    except TypeError:
        return repr(value)
    return value


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    logger.log(
        level,
        event,
        extra={key: _serialize_value(value) for key, value in fields.items() if value is not None},
    )


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "message": record.getMessage(),
        }
        for key, value in _extract_extra_fields(record).items():
            payload[key] = _serialize_value(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


class PrettyFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        timestamp = (
            datetime.fromtimestamp(record.created, tz=UTC)
            .astimezone()
            .isoformat(timespec="seconds")
        )
        parts = [timestamp, record.levelname, record.name, record.getMessage()]
        for key, value in sorted(_extract_extra_fields(record).items()):
            parts.append(f"{key}={_serialize_value(value)}")
        rendered = " ".join(parts)
        if record.exc_info:
            return f"{rendered}\n{self.formatException(record.exc_info)}"
        return rendered
