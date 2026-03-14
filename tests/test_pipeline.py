from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import src.scheduler as scheduler_module
import src.web.jobs as jobs_module
import src.web.routes.pages as pages_module
from src.db.database import create_database
from src.db.models import Event, EventTags, Source
from src.scheduler import run_notify, run_scheduled_scrape_then_tag
from src.scrapers.router import extract_domain
from src.web.jobs import JobRegistry


class DummyScraper:
    def __init__(self, events):
        self._events = events

    async def scrape(self):
        return self._events


def test_run_scheduled_scrape_then_tag_persists_job(tmp_path, monkeypatch):
    async def scenario() -> None:
        db = create_database(str(tmp_path / "pipeline.db"))
        await db.connect()
        try:
            source = Source(
                name="Example Source",
                url="https://example.com/events",
                domain=extract_domain("https://example.com/events"),
                user_id=str(uuid4()),
                status="active",
                builtin=False,
                recipe_json='{"version":1,"root_selector":"body","item_selector":".event","title_selector":".title","date_selector":".date"}',
            )
            await db.create_source(source)

            now = datetime.now(tz=UTC)
            event = Event(
                source="custom:test",
                source_url="https://example.com/event-1",
                source_id="event-1",
                title="Library Story Time",
                description="Fun for toddlers",
                location_name="Library",
                location_address="123 Main",
                location_city="Lafayette",
                start_time=now + timedelta(days=1),
                end_time=now + timedelta(days=1, hours=1),
                raw_data={},
            )

            monkeypatch.setattr(
                scheduler_module, "_build_scraper", lambda _source: DummyScraper([event])
            )

            async def fake_run_tag(db, *, progress_callback=None, include_stale=True):
                events = await db.get_recent_events(days=30)
                if progress_callback is not None:
                    await progress_callback(
                        {
                            "processed": len(events),
                            "total": len(events),
                            "succeeded": len(events),
                            "failed": 0,
                            "summary": f"{len(events)}/{len(events)} processed · {len(events)} tagged · 0 failed",
                        }
                    )
                return len(events)

            monkeypatch.setattr(scheduler_module, "run_tag", fake_run_tag)

            result = await run_scheduled_scrape_then_tag(db)
            system_user = await scheduler_module.ensure_system_user(db)
            jobs = await db.list_jobs(owner_user_id=system_user.id, limit=10)

            assert result["scraped"] == 1
            assert result["tagged"] == 1
            assert result["failed"] == 0
            assert len(jobs) == 1
            assert jobs[0].kind == "pipeline"
            assert jobs[0].state == "succeeded"
            assert jobs[0].label == "Scheduled scrape + tag job"
            assert "1 events scraped" in jobs[0].result_json
        finally:
            await db.close()

    asyncio.run(scenario())


def test_run_notify_uses_central_timezone_for_weekend_selection(tmp_path, monkeypatch):
    async def scenario() -> None:
        db = create_database(str(tmp_path / "notify-weekend.db"))
        await db.connect()
        try:
            late_friday_utc = datetime(2025, 3, 8, 4, 30, tzinfo=UTC)
            saturday_local = datetime(2025, 3, 8, 6, 0, tzinfo=UTC)
            event = Event(
                source="manual",
                source_url="https://example.com/saturday-story-time",
                source_id="saturday-story-time",
                title="Saturday Story Time",
                description="Weekend pick",
                location_name="Library",
                location_address="123 Main",
                location_city="Lafayette",
                start_time=saturday_local,
                end_time=saturday_local + timedelta(hours=1),
                scraped_at=late_friday_utc,
                raw_data={},
            )
            await db.upsert_event(event)
            await db.update_event_tags(event.id, EventTags(toddler_score=8))

            monkeypatch.setattr(
                scheduler_module,
                "current_weekend_dates",
                lambda *, now=None, roll_after_saturday_noon=False: (
                    datetime(2025, 3, 8, tzinfo=UTC).date(),
                    datetime(2025, 3, 9, tzinfo=UTC).date(),
                ),
            )

            result = await run_notify(db)

            assert result["weekend_event_count"] == 1
            assert result["ranked_event_count"] == 1
            assert result["results"]
            assert result["results"][0]["success"] is True
        finally:
            await db.close()

    asyncio.run(scenario())


def test_weekend_page_uses_central_timezone_boundary(client, monkeypatch):
    import asyncio

    late_friday_utc = datetime(2025, 3, 8, 4, 30, tzinfo=UTC)
    saturday_local = datetime(2025, 3, 8, 6, 0, tzinfo=UTC)
    weekday_local = datetime(2025, 3, 10, 18, 0, tzinfo=UTC)

    weekend_event = Event(
        source="manual",
        source_url="https://example.com/saturday-story-time",
        source_id="weekend-story-time",
        title="Saturday Story Time",
        description="Weekend pick",
        location_name="Library",
        location_address="123 Main",
        location_city="Lafayette",
        start_time=saturday_local,
        end_time=saturday_local + timedelta(hours=1),
        scraped_at=late_friday_utc,
        raw_data={},
        tags=EventTags(toddler_score=8),
    )
    weekday_event = Event(
        source="manual",
        source_url="https://example.com/monday-music",
        source_id="monday-music",
        title="Monday Music",
        description="Not a weekend event",
        location_name="Library",
        location_address="123 Main",
        location_city="Lafayette",
        start_time=weekday_local,
        end_time=weekday_local + timedelta(hours=1),
        scraped_at=late_friday_utc,
        raw_data={},
        tags=EventTags(toddler_score=9),
    )
    asyncio.run(client.app.state.db.upsert_event(weekend_event))
    asyncio.run(client.app.state.db.upsert_event(weekday_event))

    monkeypatch.setattr(
        pages_module,
        "current_weekend_dates",
        lambda *, now=None, roll_after_saturday_noon=False: (
            datetime(2025, 3, 8, tzinfo=UTC).date(),
            datetime(2025, 3, 9, tzinfo=UTC).date(),
        ),
    )

    response = client.get("/weekend")

    assert response.status_code == 200
    assert "Saturday Story Time" in response.text
    assert "Monday Music" not in response.text


def test_run_scrape_emits_structured_stage_logs(tmp_path, monkeypatch):
    async def scenario() -> None:
        emitted: list[tuple[int, str, dict[str, object]]] = []
        source = Source(
            name="Example Source",
            url="https://example.com/events",
            domain=extract_domain("https://example.com/events"),
            user_id=str(uuid4()),
            status="active",
            builtin=False,
            recipe_json=(
                '{"version":1,"root_selector":"body","item_selector":".event",'
                '"title_selector":".title","date_selector":".date"}'
            ),
        )
        now = datetime.now(tz=UTC)
        event = Event(
            source="custom:test",
            source_url="https://example.com/event-1",
            source_id="event-1",
            title="Library Story Time",
            description="Fun for toddlers",
            location_name="Library",
            location_address="123 Main",
            location_city="Lafayette",
            start_time=now + timedelta(days=1),
            end_time=now + timedelta(days=1, hours=1),
            raw_data={},
        )

        def capture(level: int, event: str, **context: object) -> None:
            emitted.append((level, event, context))

        monkeypatch.setattr(
            scheduler_module, "_build_scraper", lambda _source: DummyScraper([event])
        )
        monkeypatch.setattr(scheduler_module, "_runtime_log", capture)

        async with create_database(str(tmp_path / "pipeline-logs.db")) as db:
            await db.create_source(source)
            result = await scheduler_module.run_scrape(db)

        assert result == 1

        started = [context for _, event, context in emitted if event == "pipeline_stage_started"]
        source_started = [
            context for _, event, context in emitted if event == "pipeline_scrape_source_started"
        ]
        source_finished = [
            context for _, event, context in emitted if event == "pipeline_scrape_source_succeeded"
        ]
        finished = [context for _, event, context in emitted if event == "pipeline_stage_succeeded"]

        assert len(started) == 1
        assert started[0]["stage"] == "scrape"
        assert started[0]["source_count"] == 1
        assert len(source_started) == 1
        assert source_started[0]["source_id"] == source.id
        assert source_started[0]["source_name"] == source.name
        assert len(source_finished) == 1
        assert source_finished[0]["source_id"] == source.id
        assert source_finished[0]["event_count"] == 1
        assert source_finished[0]["duration_ms"] >= 0
        assert len(finished) == 1
        assert finished[0]["stage"] == "scrape"
        assert finished[0]["scraped"] == 1
        assert finished[0]["duration_ms"] >= 0

    asyncio.run(scenario())


def test_job_registry_failure_logs_include_job_context(tmp_path, monkeypatch):
    async def scenario() -> None:
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'job-logging.db'}"
        registry = JobRegistry()
        emitted: list[tuple[int, str, dict[str, object]]] = []

        async def failing_runner(_context):
            raise ValueError("boom")

        def capture(level: int, event: str, **context: object) -> None:
            emitted.append((level, event, context))

        monkeypatch.setattr(jobs_module, "_runtime_log", capture)
        job, created = await registry.start_unique(
            kind="pipeline",
            job_key="pipeline:test-failure",
            label="Pipeline failure test",
            owner_user_id="owner-1",
            source_id="source-123",
            runner=failing_runner,
            database_url=database_url,
        )

        assert created is True

        task = registry._active_by_id[job.id].task
        await task

        async with create_database(database_url=database_url) as db:
            persisted = await db.get_job(job.id)

        assert persisted is not None
        assert persisted.state == "failed"
        assert persisted.error == "boom"

        started = [context for _, event, context in emitted if event == "background_job_started"]
        failed = [context for _, event, context in emitted if event == "background_job_failed"]

        assert len(started) == 1
        assert started[0]["job_id"] == job.id
        assert started[0]["job_key"] == "pipeline:test-failure"
        assert started[0]["source_id"] == "source-123"

        assert len(failed) == 1
        assert failed[0]["job_id"] == job.id
        assert failed[0]["job_key"] == "pipeline:test-failure"
        assert failed[0]["source_id"] == "source-123"
        assert failed[0]["error_type"] == "ValueError"
        assert failed[0]["error_message"] == "boom"
        assert failed[0]["duration_ms"] >= 0

    asyncio.run(scenario())
