from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from src.db.models import Event, EventTags
from tests.postgres_test_helpers import run_database_method


def _create_event(
    database_url: str,
    *,
    title: str,
    city: str = "Lafayette",
    start_time: datetime | None = None,
    tags: EventTags | None = None,
) -> Event:
    now = datetime.now(tz=UTC)
    event_start = start_time or (now + timedelta(days=1))
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
        start_time=event_start,
        end_time=event_start + timedelta(hours=2),
        scraped_at=now,
        raw_data={},
        tags=tags,
    )
    run_database_method(database_url, "upsert_event", event)
    return event


def test_events_page_hx_filter_request_returns_partial(client) -> None:
    _create_event(client.app.state.db.database_url, title="Tennis Clinic")

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
    _create_event(client.app.state.db.database_url, title="Tennis Clinic")

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
    _create_event(client.app.state.db.database_url, title="Tennis Clinic")

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
    from tests.test_security import login

    user = create_user(email="events-api@example.com", home_city="Baton Rouge")
    login(client, email=user.email)

    database_url = client.app.state.db.database_url
    first = _create_event(database_url, title="Alpha Story Time", city="Lafayette")
    second = _create_event(database_url, title="Zoo Morning", city="Baton Rouge")
    run_database_method(database_url, "set_event_attended", user.id, second.id, True)
    run_database_method(database_url, "set_event_saved", user.id, second.id, True)

    response = client.get(
        "/api/events",
        params={
            "page": 1,
            "per_page": 1,
            "city": "Baton Rouge",
            "attended": "yes",
            "saved": "yes",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["pagination"] == {"page": 1, "per_page": 1, "total": 1, "total_pages": 1}
    assert payload["filters"]["city"] == "Baton Rouge"
    assert payload["filters"]["attended"] == "yes"
    assert payload["filters"]["saved"] == "yes"
    assert len(payload["items"]) == 1
    assert payload["items"][0]["id"] == second.id
    assert payload["items"][0]["title"] == "Zoo Morning"
    assert payload["items"][0]["city"] == "Baton Rouge"
    assert payload["items"][0]["city_slug"] == "baton-rouge"
    assert payload["items"][0]["viewer_state"] == {"saved": True, "attended": True}
    assert all(item["id"] != first.id for item in payload["items"])


def test_api_events_rejects_invalid_filter_values(client, create_user) -> None:
    from tests.test_security import login

    user = create_user(email="events-invalid@example.com")
    login(client, email=user.email)

    response = client.get("/api/events", params={"tagged": "maybe"})

    assert response.status_code == 422
    assert response.json() == {"detail": "tagged must be yes or no"}


def test_events_logged_in_default_scope_is_nearby_and_city_override_wins(
    client, create_user
) -> None:
    from tests.test_security import login

    user = create_user(email="nearby@example.com", home_city="Lafayette")
    login(client, email=user.email)

    database_url = client.app.state.db.database_url
    lafayette = _create_event(database_url, title="Lafayette Story Time", city="Lafayette")
    san_francisco = _create_event(database_url, title="Golden Gate Kids Day", city="San Francisco")

    nearby = client.get("/events")
    assert nearby.status_code == 200
    assert lafayette.title in nearby.text
    assert san_francisco.title not in nearby.text

    all_cities = client.get("/events", params={"scope": "all"})
    assert all_cities.status_code == 200
    assert lafayette.title in all_cities.text
    assert san_francisco.title in all_cities.text

    explicit_city = client.get("/events", params={"city": "San Francisco"})
    assert explicit_city.status_code == 200
    assert san_francisco.title in explicit_city.text
    assert lafayette.title not in explicit_city.text


def test_my_events_shows_saved_and_attended_across_all_cities(client, create_user) -> None:
    from tests.test_security import login

    user = create_user(email="my-events@example.com", home_city="Lafayette")
    login(client, email=user.email)

    database_url = client.app.state.db.database_url
    saved_event = _create_event(database_url, title="Saved Austin Zoo", city="Austin")
    attended_event = _create_event(
        database_url, title="Attended Bay Story Time", city="San Francisco"
    )
    run_database_method(database_url, "set_event_saved", user.id, saved_event.id, True)
    run_database_method(database_url, "set_event_attended", user.id, attended_event.id, True)

    response = client.get("/my-events")

    assert response.status_code == 200
    assert saved_event.title in response.text
    assert attended_event.title in response.text


def test_shared_corpus_is_hidden_by_default_for_other_users_but_visible_in_all_scope(
    client, create_user
) -> None:
    from tests.test_security import login

    create_user(email="sf-parent@example.com", home_city="San Francisco")
    louisiana_user = create_user(email="la-parent@example.com", home_city="Lafayette")

    database_url = client.app.state.db.database_url
    sf_event = _create_event(database_url, title="Golden Gate Play Day", city="San Francisco")
    lafayette_event = _create_event(database_url, title="Acadiana Story Time", city="Lafayette")

    login(client, email=louisiana_user.email)

    nearby = client.get("/events")
    assert nearby.status_code == 200
    assert lafayette_event.title in nearby.text
    assert sf_event.title not in nearby.text

    all_cities = client.get("/events", params={"scope": "all"})
    assert all_cities.status_code == 200
    assert sf_event.title in all_cities.text

    explicit_city = client.get("/events", params={"city": "San Francisco"})
    assert explicit_city.status_code == 200
    assert sf_event.title in explicit_city.text


def test_calendar_logged_in_defaults_to_nearby_scope(client, create_user) -> None:
    from tests.test_security import login

    user = create_user(email="calendar-scope@example.com", home_city="Lafayette")
    login(client, email=user.email)

    now = datetime.now(tz=UTC)
    database_url = client.app.state.db.database_url
    lafayette_event = _create_event(
        database_url,
        title="Calendar Lafayette",
        city="Lafayette",
        start_time=now + timedelta(days=2),
    )
    san_francisco_event = _create_event(
        database_url,
        title="Calendar San Francisco",
        city="San Francisco",
        start_time=now + timedelta(days=3),
    )
    month = (now + timedelta(days=2)).strftime("%Y-%m")

    response = client.get("/calendars", params={"month": month})

    assert response.status_code == 200
    assert lafayette_event.title in response.text
    assert san_francisco_event.title not in response.text

    response_all = client.get("/calendars", params={"month": month, "scope": "all"})
    assert response_all.status_code == 200
    assert san_francisco_event.title in response_all.text


def test_weekend_logged_in_defaults_to_nearby_scope(client, create_user) -> None:
    from src.timezones import current_weekend_dates, weekend_window_utc
    from tests.test_security import login

    user = create_user(email="weekend-scope@example.com", home_city="Lafayette")
    login(client, email=user.email)

    saturday, sunday = current_weekend_dates()
    weekend_start, _weekend_end = weekend_window_utc(saturday, sunday)
    weekend_start = weekend_start + timedelta(hours=6)
    database_url = client.app.state.db.database_url
    nearby_event = _create_event(
        database_url,
        title="Weekend Lafayette",
        city="Lafayette",
        start_time=weekend_start,
        tags=EventTags(toddler_score=8),
    )
    far_event = _create_event(
        database_url,
        title="Weekend San Francisco",
        city="San Francisco",
        start_time=weekend_start + timedelta(hours=1),
        tags=EventTags(toddler_score=7),
    )

    response = client.get("/weekend")

    assert response.status_code == 200
    assert nearby_event.title in response.text
    assert far_event.title not in response.text

    response_all = client.get("/weekend", params={"scope": "all"})
    assert response_all.status_code == 200
    assert far_event.title in response_all.text
