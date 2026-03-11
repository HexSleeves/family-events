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


def test_events_page_renders_query_in_global_search_inputs(client) -> None:
    import asyncio

    asyncio.run(_create_event(client, title="Tennis Clinic"))

    response = client.get("/events", params={"q": "Tennis"})

    assert response.status_code == 200
    assert response.text.count("data-global-event-search") >= 2
    assert response.text.count('value="Tennis"') >= 2


def test_health_includes_pipeline_freshness(client) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["checks"]["database"]["ok"] is True
    assert "pipeline" in payload["checks"]
    assert "latest_scraped_at" in payload["checks"]["pipeline"]
    assert "latest_tagged_at" in payload["checks"]["pipeline"]
    assert "latest_notified_at" in payload["checks"]["pipeline"]
    assert payload["checks"]["pipeline"]["stuck_running_jobs"] == 0


def test_api_events_requires_login(client) -> None:
    response = client.get("/api/events")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


def test_api_events_returns_paginated_filtered_payload(client, create_user) -> None:
    import asyncio

    from tests.test_security import login

    user = create_user(email="events-api@example.com")
    login(client, email=user.email)

    first = asyncio.run(_create_event(client, title="Alpha Story Time", city="Lafayette"))
    second = asyncio.run(_create_event(client, title="Zoo Morning", city="Baton Rouge"))
    asyncio.run(client.app.state.db.mark_attended(second.id))

    response = client.get(
        "/api/events",
        params={"page": 1, "per_page": 1, "city": "Baton Rouge", "attended": "yes"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["pagination"] == {"page": 1, "per_page": 1, "total": 1, "total_pages": 1}
    assert payload["filters"]["city"] == "Baton Rouge"
    assert payload["filters"]["attended"] == "yes"
    assert len(payload["items"]) == 1
    assert payload["items"][0]["id"] == second.id
    assert payload["items"][0]["title"] == "Zoo Morning"
    assert payload["items"][0]["attended"] is True
    assert payload["items"][0]["city"] == "Baton Rouge"
    assert all(item["id"] != first.id for item in payload["items"])


def test_api_events_rejects_invalid_filter_values(client, create_user) -> None:
    from tests.test_security import login

    user = create_user(email="events-invalid@example.com")
    login(client, email=user.email)

    response = client.get("/api/events", params={"tagged": "maybe"})

    assert response.status_code == 422
    assert response.json() == {"detail": "tagged must be yes or no"}
