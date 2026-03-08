import asyncio
from datetime import datetime

from src.db.models import Event
from src.tagger.llm import EventTagger


def _event(idx: int) -> Event:
    return Event(
        title=f"Event {idx}",
        description="Fun for families",
        start_time=datetime(2026, 3, 8, 10, 0),
        end_time=None,
        source="test",
        source_url=f"https://example.com/{idx}",
        source_id="test-source",
        location_name="Park",
        location_address="123 Main St",
        location_city="Lafayette",
        is_free=True,
        price_min=None,
        price_max=None,
        tags=None,
        raw_data={},
    )


class _FakeTagger(EventTagger):
    def __init__(self) -> None:
        super().__init__()
        self._concurrency = 2
        self.current = 0
        self.peak = 0

    async def tag_event(self, event: Event):
        self.current += 1
        self.peak = max(self.peak, self.current)
        await asyncio.sleep(0.01)
        self.current -= 1
        return self._heuristic_tag(event)


def test_tag_events_respects_concurrency():
    tagger = _FakeTagger()
    events = [_event(i) for i in range(6)]

    results = asyncio.run(tagger.tag_events(events))

    assert len(results) == 6
    assert tagger.peak <= 2


def test_tag_events_in_batches_calls_callback():
    tagger = _FakeTagger()
    events = [_event(i) for i in range(5)]
    callbacks: list[tuple[int, int, int]] = []

    async def scenario() -> None:
        async def on_batch_complete(start_idx, batch, tagged_batch, _all_results):
            callbacks.append((start_idx, len(batch), len(tagged_batch)))

        results = await tagger.tag_events_in_batches(
            events,
            batch_size=2,
            on_batch_complete=on_batch_complete,
        )
        assert len(results) == 5

    asyncio.run(scenario())

    assert callbacks == [(0, 2, 2), (2, 2, 2), (4, 1, 1)]


def test_rule_based_tagger_flags_adult_event_low_score():
    event = Event(
        title="Brewery Trivia Night",
        description="Adults only trivia with beer specials",
        start_time=datetime(2026, 3, 8, 20, 0),
        end_time=None,
        source="test",
        source_url="https://example.com/adult",
        source_id="test-source",
        location_name="Taproom",
        location_address="123 Main St",
        location_city="Lafayette",
        is_free=True,
        price_min=None,
        price_max=None,
        tags=None,
        raw_data={},
    )

    tags = EventTagger()._heuristic_tag(event)

    assert tags.audience == "adult_skewed"
    assert tags.toddler_score <= 3
    assert tags.raw_rule_score <= 30
    assert tags.exclusion_signals



def test_rule_based_tagger_rewards_toddler_event_high_score():
    event = Event(
        title="Toddler Story Time and Sensory Play",
        description="Library storytime for toddlers with songs and sensory bins.",
        start_time=datetime(2026, 3, 8, 10, 0),
        end_time=None,
        source="test",
        source_url="https://example.com/toddler",
        source_id="test-source",
        location_name="Library",
        location_address="123 Main St",
        location_city="Lafayette",
        is_free=True,
        price_min=None,
        price_max=None,
        tags=None,
        raw_data={},
    )

    tags = EventTagger()._heuristic_tag(event)

    assert tags.audience == "toddler_focused"
    assert tags.toddler_score >= 8
    assert tags.raw_rule_score >= 75
    assert "learning" in tags.categories


def test_get_untagged_events_includes_stale_tagging_versions(tmp_path):
    async def scenario() -> None:
        from src.db.database import Database
        from src.db.models import Event, EventTags
        from src.tagger.taxonomy import TAGGING_VERSION

        db = Database(str(tmp_path / "tagging-version.db"))
        await db.connect()
        try:
            stale = Event(
                title="Old tagged event",
                description="Fun",
                start_time=datetime(2026, 3, 8, 10, 0),
                end_time=None,
                source="test",
                source_url="https://example.com/stale",
                source_id="stale",
                location_name="Park",
                location_address="123 Main St",
                location_city="Lafayette",
                is_free=True,
                tags=EventTags(tagging_version="v1"),
                raw_data={},
            )
            fresh = Event(
                title="Fresh tagged event",
                description="Fun",
                start_time=datetime(2026, 3, 8, 11, 0),
                end_time=None,
                source="test",
                source_url="https://example.com/fresh",
                source_id="fresh",
                location_name="Park",
                location_address="123 Main St",
                location_city="Lafayette",
                is_free=True,
                tags=EventTags(tagging_version=TAGGING_VERSION),
                raw_data={},
            )
            await db.upsert_event(stale)
            await db.upsert_event(fresh)

            events = await db.get_untagged_events(tagging_version=TAGGING_VERSION)
            ids = {event.source_id for event in events}
            assert "stale" in ids
            assert "fresh" not in ids
        finally:
            await db.close()

    asyncio.run(scenario())


def test_count_stale_tagged_events(tmp_path):
    async def scenario() -> None:
        from src.db.database import Database
        from src.db.models import Event, EventTags
        from src.tagger.taxonomy import TAGGING_VERSION

        db = Database(str(tmp_path / "stale-count.db"))
        await db.connect()
        try:
            await db.upsert_event(
                Event(
                    title="Old tagged event",
                    description="Fun",
                    start_time=datetime(2026, 3, 8, 10, 0),
                    end_time=None,
                    source="test",
                    source_url="https://example.com/stale-2",
                    source_id="stale-2",
                    location_name="Park",
                    location_address="123 Main St",
                    location_city="Lafayette",
                    is_free=True,
                    tags=EventTags(tagging_version="v1"),
                    raw_data={},
                )
            )
            await db.upsert_event(
                Event(
                    title="Fresh tagged event",
                    description="Fun",
                    start_time=datetime(2026, 3, 8, 11, 0),
                    end_time=None,
                    source="test",
                    source_url="https://example.com/fresh-2",
                    source_id="fresh-2",
                    location_name="Park",
                    location_address="123 Main St",
                    location_city="Lafayette",
                    is_free=True,
                    tags=EventTags(tagging_version=TAGGING_VERSION),
                    raw_data={},
                )
            )

            assert await db.count_stale_tagged_events(tagging_version=TAGGING_VERSION) == 1
        finally:
            await db.close()

    asyncio.run(scenario())
