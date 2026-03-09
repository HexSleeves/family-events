from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import src.scheduler as scheduler_module
from src.db.database import create_database
from src.db.models import Event, Source
from src.scheduler import run_scheduled_scrape_then_tag
from src.scrapers.router import extract_domain


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

            monkeypatch.setattr(scheduler_module, "_build_scraper", lambda _source: DummyScraper([event]))

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
