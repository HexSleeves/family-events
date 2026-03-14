from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.db.models import Event, Source
from src.scrapers.router import extract_domain
from tests.postgres_test_helpers import run_database_method
from tests.test_security import extract_csrf_token, login


def _create_source(
    database_url: str,
    *,
    user_id: str,
    name: str = "Example Source",
    url: str = "https://example.com/events",
    status: str = "active",
    recipe_json: str | None = None,
) -> Source:
    source = Source(
        name=name,
        url=url,
        domain=extract_domain(url),
        user_id=user_id,
        status=status,
        recipe_json=recipe_json,
    )
    run_database_method(database_url, "create_source", source)
    return source


def test_sources_page_redirects_when_logged_out(client):
    response = client.get("/sources", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_source_detail_forbids_other_users_source(client, create_user):
    owner = create_user(email="source-owner@example.com")
    intruder = create_user(email="source-intruder@example.com")
    source = _create_source(client.app.state.db.database_url, user_id=owner.id)

    login(client, email=intruder.email)

    response = client.get(f"/source/{source.id}")

    assert response.status_code == 403
    assert response.text == "Forbidden"


def test_source_detail_shows_recent_events_for_owner(client, create_user):
    user = create_user(email="source-detail@example.com")
    database_url = client.app.state.db.database_url
    source = _create_source(database_url, user_id=user.id, name="Neighborhood Calendar")
    event = Event(
        source=f"custom:{source.id}",
        source_url="https://example.com/events/story-time",
        source_id="evt-1",
        title="Neighborhood Story Time",
        location_name="Library",
        location_city="Lafayette",
        start_time=datetime.now(tz=UTC) + timedelta(days=1),
    )
    run_database_method(database_url, "upsert_event", event)

    login(client, email=user.email)

    response = client.get(f"/source/{source.id}")

    assert response.status_code == 200
    assert "Neighborhood Calendar" in response.text
    assert source.url in response.text
    assert event.title in response.text


def test_add_predefined_source_requires_login(client):
    response = client.post("/api/sources/predefined", data={"source_key": "baton-rouge-brec"})

    assert response.status_code == 401
    assert "Please log in first" in response.headers.get("hx-trigger", "")


def test_add_predefined_source_adds_source_for_user(client, create_user):
    user = create_user(email="predefined-source@example.com", home_city="Baton Rouge")
    login(client, email=user.email)
    page = client.get("/sources")
    csrf_token = extract_csrf_token(page.text)

    response = client.post(
        "/api/sources/predefined",
        data={"csrf_token": csrf_token, "source_key": "baton-rouge-brec"},
    )

    assert response.status_code == 200
    assert "Added BREC Parks" in response.headers.get("hx-trigger", "")
    sources = run_database_method(client.app.state.db.database_url, "get_user_sources", user.id)
    assert len(sources) == 1
    assert sources[0].url == "https://www.brec.org/calendar"


@pytest.mark.parametrize(
    ("method", "path_template", "recipe_json"),
    [
        ("POST", "/api/sources/{source_id}/toggle", None),
        ("DELETE", "/api/sources/{source_id}", None),
        ("POST", "/api/sources/{source_id}/analyze", None),
        ("POST", "/api/sources/{source_id}/test", "{}"),
    ],
)
def test_source_mutations_forbid_other_users(
    client,
    create_user,
    method: str,
    path_template: str,
    recipe_json: str | None,
):
    owner = create_user(email=f"owner-{method.lower()}@example.com")
    intruder = create_user(email=f"intruder-{method.lower()}@example.com")
    source = _create_source(
        client.app.state.db.database_url,
        user_id=owner.id,
        recipe_json=recipe_json,
    )

    login(client, email=intruder.email)
    page = client.get("/sources")
    csrf_token = extract_csrf_token(page.text)

    response = client.request(
        method,
        path_template.format(source_id=source.id),
        data={"csrf_token": csrf_token},
    )

    assert response.status_code == 403
    assert response.text == "Forbidden"
    persisted = run_database_method(client.app.state.db.database_url, "get_source", source.id)
    assert persisted is not None
    assert persisted.user_id == owner.id
