from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.observability import PrettyFormatter
from src.scrapers.recipe import JSONLDStrategy, ScrapeRecipe


class FakeSourceJobDb:
    def __init__(self, source: object | None = None) -> None:
        self.source = source
        self.recipe_updates: list[dict[str, object]] = []
        self.status_updates: list[dict[str, object]] = []

    async def update_source_recipe(self, source_id: str, recipe_json: str, *, status: str) -> None:
        self.recipe_updates.append(
            {"source_id": source_id, "recipe_json": recipe_json, "status": status}
        )

    async def update_source_status(self, source_id: str, *, status: str, error: str = "") -> None:
        self.status_updates.append({"source_id": source_id, "status": status, "error": error})

    async def get_source(self, source_id: str) -> object | None:
        assert self.source is None or getattr(self.source, "id", source_id) == source_id
        return self.source


@contextmanager
def capture_uvicorn_logs(level: int = logging.INFO):
    logger = logging.getLogger("uvicorn.error")
    messages: list[str] = []

    class _Handler(logging.Handler):
        def __init__(self) -> None:
            super().__init__(level=level)
            self.setFormatter(PrettyFormatter())

        def emit(self, record: logging.LogRecord) -> None:
            messages.append(self.format(record))

    handler = _Handler()
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(min(previous_level, level) if previous_level else level)
    try:
        yield messages
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


def test_run_source_analyze_job_logs_success(monkeypatch):
    import src.web.routes.sources as sources_module

    recipe = ScrapeRecipe(
        strategy="jsonld",
        analyzed_at=datetime.now(tz=UTC),
        confidence=0.9,
        notes="strong signal",
        jsonld=JSONLDStrategy(),
    )
    job_db = FakeSourceJobDb()

    class FakeAnalyzer:
        async def analyze(self, url: str) -> ScrapeRecipe:
            assert url == "https://example.com/events"
            return recipe

    monkeypatch.setattr(sources_module, "PageAnalyzer", lambda: FakeAnalyzer())

    async def scenario() -> None:
        with capture_uvicorn_logs() as messages:
            result = await sources_module._run_source_analyze_job(
                job_db,
                source_id="source-1",
                source_name="Library Calendar",
                source_url="https://example.com/events",
            )

        assert result["strategy"] == "jsonld"
        assert result["confidence"] == recipe.confidence
        assert any(
            "source_job_runner_started" in message
            and "action=analyze" in message
            and "source_id=source-1" in message
            for message in messages
        )
        assert any(
            "source_job_runner_succeeded" in message
            and "strategy=jsonld" in message
            and "status=active" in message
            for message in messages
        )

    asyncio.run(scenario())
    assert job_db.recipe_updates and job_db.recipe_updates[0]["status"] == "active"


def test_run_source_analyze_job_logs_failure(monkeypatch):
    import src.web.routes.sources as sources_module

    job_db = FakeSourceJobDb()

    class FakeAnalyzer:
        async def analyze(self, url: str) -> ScrapeRecipe:
            raise RuntimeError("analysis exploded")

    monkeypatch.setattr(sources_module, "PageAnalyzer", lambda: FakeAnalyzer())

    async def scenario() -> None:
        with (
            capture_uvicorn_logs() as messages,
            pytest.raises(RuntimeError, match="analysis exploded"),
        ):
            await sources_module._run_source_analyze_job(
                job_db,
                source_id="source-1",
                source_name="Library Calendar",
                source_url="https://example.com/events",
            )

        assert any(
            "source_job_runner_failed" in message
            and "action=analyze" in message
            and "source_id=source-1" in message
            and "error_message=analysis exploded" in message
            for message in messages
        )

    asyncio.run(scenario())
    assert job_db.status_updates == [
        {"source_id": "source-1", "status": "failed", "error": "analysis exploded"}
    ]


def test_run_source_test_job_logs_success(monkeypatch):
    import src.web.routes.sources as sources_module

    recipe = ScrapeRecipe(
        strategy="jsonld",
        analyzed_at=datetime.now(tz=UTC),
        confidence=0.8,
        notes="works",
        jsonld=JSONLDStrategy(),
    )
    source = SimpleNamespace(
        id="source-1",
        name="Library Calendar",
        url="https://example.com/events",
        recipe_json=recipe.model_dump_json(),
    )
    job_db = FakeSourceJobDb(source=source)

    class FakeScraper:
        def __init__(self, *, url: str, source_id: str, recipe: ScrapeRecipe) -> None:
            assert url == source.url
            assert source_id == source.id
            assert recipe.strategy == "jsonld"

        async def scrape(self) -> list[object]:
            return [
                SimpleNamespace(
                    title="Story Time",
                    start_time=datetime(2026, 3, 14, 10, 0, tzinfo=UTC),
                    location_name="Library",
                    location_city="Lafayette",
                    source_url="https://example.com/events/story-time",
                )
            ]

    monkeypatch.setattr(sources_module, "GenericScraper", FakeScraper)

    async def scenario() -> None:
        with capture_uvicorn_logs() as messages:
            result = await sources_module._run_source_test_job(
                job_db,
                source_id="source-1",
            )

        assert result["count"] == 1
        assert result["strategy"] == "jsonld"
        assert any(
            "source_job_runner_started" in message
            and "action=test" in message
            and "source_id=source-1" in message
            for message in messages
        )
        assert any(
            "source_job_runner_succeeded" in message
            and "event_count=1" in message
            and "strategy=jsonld" in message
            for message in messages
        )

    asyncio.run(scenario())


def test_run_logged_pipeline_job_logs_success_and_failure():
    import src.web.routes.pipeline as pipeline_module

    async def success_operation() -> dict[str, object]:
        return {
            "summary": "2 deliveries succeeded",
            "results": [{"success": True}, {"success": True}],
        }

    async def failure_operation() -> int:
        raise RuntimeError("notify exploded")

    async def scenario() -> None:
        with capture_uvicorn_logs() as messages:
            result = await pipeline_module._run_logged_pipeline_job(
                job_kind="notify",
                user_id="user-1",
                operation_name="run_notify",
                operation=success_operation,
            )
            assert result["summary"] == "2 deliveries succeeded"

            with pytest.raises(RuntimeError, match="notify exploded"):
                await pipeline_module._run_logged_pipeline_job(
                    job_kind="notify",
                    user_id="user-1",
                    operation_name="run_notify",
                    operation=failure_operation,
                )

        assert any(
            "pipeline_job_runner_started" in message
            and "job_kind=notify" in message
            and "operation=run_notify" in message
            and "user_id=user-1" in message
            for message in messages
        )
        assert any(
            "pipeline_job_runner_succeeded" in message
            and "summary=2 deliveries succeeded" in message
            and "success_count=2" in message
            for message in messages
        )
        assert any(
            "pipeline_job_runner_failed" in message
            and "operation=run_notify" in message
            and "error_message=notify exploded" in message
            for message in messages
        )

    asyncio.run(scenario())
