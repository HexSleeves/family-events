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
