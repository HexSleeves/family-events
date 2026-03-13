from __future__ import annotations

import importlib
import importlib.util
import json
import logging


def test_observability_module_exposes_format_resolution() -> None:
    spec = importlib.util.find_spec("src.observability")

    assert spec is not None

    observability = importlib.import_module("src.observability")

    assert observability.resolve_log_format("auto", "development") == "pretty"
    assert observability.resolve_log_format("auto", "production") == "json"
    assert observability.resolve_log_format("json", "development") == "json"
    assert observability.resolve_log_format("pretty", "production") == "pretty"


def test_json_formatter_serializes_structured_fields() -> None:
    observability = importlib.import_module("src.observability")

    formatter = observability.JsonFormatter()
    record = logging.makeLogRecord(
        {
            "name": "family-events",
            "levelname": "INFO",
            "msg": "request_complete",
            "pathname": __file__,
            "lineno": 1,
            "path": "/health",
            "status_code": 200,
        }
    )

    payload = json.loads(formatter.format(record))

    assert payload["message"] == "request_complete"
    assert payload["logger"] == "family-events"
    assert payload["path"] == "/health"
    assert payload["status_code"] == 200


def test_pretty_formatter_renders_readable_key_value_fields() -> None:
    observability = importlib.import_module("src.observability")

    formatter = observability.PrettyFormatter()
    record = logging.makeLogRecord(
        {
            "name": "family-events",
            "levelname": "INFO",
            "msg": "request_complete",
            "pathname": __file__,
            "lineno": 1,
            "path": "/health",
            "status_code": 200,
        }
    )

    rendered = formatter.format(record)

    assert "INFO" in rendered
    assert "request_complete" in rendered
    assert "path=/health" in rendered
    assert "status_code=200" in rendered
