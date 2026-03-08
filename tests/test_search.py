from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from src.db.models import Event


async def _create_event(client, *, title: str, city: str = "Lafayette") -> Event:
    now = datetime.now(tz=UTC)
    event = Event(
        id=str(uuid4()),
        source="manual",
        source_url=f"https://example.com/{uuid4()}",
        source_id=str(uuid4()),
        title=title,
        description="Searchable description",
        location_name="Test Venue",
        location_address="123 Main St",
        location_city=city,
        start_time=now + timedelta(days=1),
        end_time=now + timedelta(days=1, hours=2),
        scraped_at=now,
        raw_data={},
    )
    await client.app.state.db.upsert_event(event)
    return event


def test_events_page_hx_filter_request_returns_partial(client) -> None:
    import asyncio

    asyncio.run(_create_event(client, title="Tennis Clinic"))

    response = client.get(
        "/events",
        params={"q": "Tennis"},
        headers={"HX-Request": "true", "HX-Target": "events-results"},
    )

    assert response.status_code == 200
    assert 'id="events-results"' not in response.text
    assert "Tennis Clinic" in response.text
    assert "Browse Events" not in response.text


def test_events_page_hx_global_search_returns_full_page(client) -> None:
    import asyncio

    asyncio.run(_create_event(client, title="Tennis Clinic"))

    response = client.get(
        "/events",
        params={"q": "Tennis"},
        headers={"HX-Request": "true", "HX-Target": "body"},
    )

    assert response.status_code == 200
    assert 'id="main-content"' in response.text
    assert "Browse Events" in response.text
    assert "Tennis Clinic" in response.text
