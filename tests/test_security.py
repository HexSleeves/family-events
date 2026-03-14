from __future__ import annotations

import asyncio
import re
import socket
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from src.db.database import create_database
from src.db.models import Event, Source
from src.scheduler import SYSTEM_USER_EMAIL, ensure_system_user
from src.scrapers.analyzer import validate_public_http_url
from src.scrapers.router import extract_domain
from src.web.auth import verify_password
from src.web.common import _same_origin
from tests.postgres_test_helpers import run_database_method


def extract_csrf_token(html: str) -> str:
    match = re.search(r'name="csrf-token" content="([^"]+)"', html)
    assert match, "csrf token meta tag not found"
    return match.group(1)


def login(client, email: str = "parent@example.com", password: str = "Password123") -> str:
    page = client.get("/login")
    csrf_token = extract_csrf_token(page.text)
    response = client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert response.status_code == 302
    return csrf_token


def test_login_sets_hardened_session_cookie(client, create_user):
    create_user()
    page = client.get("/login")
    csrf_token = extract_csrf_token(page.text)

    response = client.post(
        "/login",
        data={"email": "parent@example.com", "password": "Password123", "csrf_token": csrf_token},
        follow_redirects=False,
    )

    set_cookie = response.headers["set-cookie"].lower()
    assert "session=" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "secure" in set_cookie


def test_mutating_endpoint_rejects_missing_csrf(client, create_user):
    create_user()
    login(client)

    response = client.post("/api/profile/theme", data={"theme": "dark"})

    assert response.status_code == 403
    assert "Security check failed" in response.headers.get("hx-trigger", "")


def test_mutating_endpoint_rejects_cross_origin(client, create_user):
    create_user()
    login(client)
    profile = client.get("/profile")
    csrf_token = extract_csrf_token(profile.text)

    response = client.post(
        "/api/profile/theme",
        data={"theme": "dark", "csrf_token": csrf_token},
        headers={"Origin": "https://evil.example"},
    )

    assert response.status_code == 403
    assert "origin policy" in response.headers.get("hx-trigger", "")


def test_same_origin_accepts_loopback_aliases():
    assert _same_origin("http://127.0.0.1:8000", "http://localhost:8000")
    assert _same_origin("http://localhost:8000", "http://[::1]:8000")
    assert not _same_origin("http://127.0.0.1:8000", "https://127.0.0.1:8000")


def test_logout_requires_valid_csrf(client, create_user):
    create_user()
    login(client)

    denied = client.post("/logout", data={})
    assert denied.status_code == 403

    profile = client.get("/profile")
    csrf_token = extract_csrf_token(profile.text)
    response = client.post("/logout", data={"csrf_token": csrf_token}, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/"


def test_profile_page_redirects_when_logged_out(client):
    response = client.get("/profile", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_login_rate_limit_applies(client, create_user):
    create_user()
    from src.web import app as appmod

    appmod.settings.auth_rate_limit_max_requests = 2
    appmod.settings.auth_rate_limit_window_seconds = 60
    appmod._rate_limit_store.clear()

    for _ in range(2):
        page = client.get("/login")
        csrf_token = extract_csrf_token(page.text)
        response = client.post(
            "/login",
            data={
                "email": "parent@example.com",
                "password": "wrong-password",
                "csrf_token": csrf_token,
            },
        )
        assert response.status_code == 200

    page = client.get("/login")
    csrf_token = extract_csrf_token(page.text)
    blocked = client.post(
        "/login",
        data={
            "email": "parent@example.com",
            "password": "wrong-password",
            "csrf_token": csrf_token,
        },
    )
    assert blocked.status_code == 429
    assert "Too many login attempts" in blocked.headers.get("hx-trigger", "")


def test_validate_public_http_url_blocks_private_dns_resolution(monkeypatch):
    def fake_getaddrinfo(host: str, port, *args, **kwargs):
        assert host == "internal.example"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    try:
        validate_public_http_url("https://internal.example/events")
    except ValueError as exc:
        assert "Private or local network URLs are not allowed" in str(exc)
    else:
        raise AssertionError("expected private DNS resolution to be rejected")


def test_signup_rejects_missing_csrf(client):
    response = client.post(
        "/signup",
        data={
            "email": "new@example.com",
            "display_name": "New Parent",
            "password": "Password123",
            "confirm_password": "Password123",
            "home_city": "Lafayette",
            "child_name": "Em",
            "temperament": "curious",
        },
    )

    assert response.status_code == 403
    assert "Security check failed" in response.headers.get("hx-trigger", "")


def test_signup_creates_onboarded_user_and_predefined_sources(client):
    page = client.get("/signup")
    csrf_token = extract_csrf_token(page.text)

    response = client.post(
        "/signup",
        data={
            "csrf_token": csrf_token,
            "email": "new@example.com",
            "display_name": "New Parent",
            "password": "Password123",
            "confirm_password": "Password123",
            "home_city": "Baton Rouge",
            "preferred_cities": "Baton Rouge, Lafayette",
            "child_name": "Em",
            "temperament": "curious but sensitive to noise",
            "child_age_years": "3",
            "child_age_months": "6",
            "loves": "animals, music",
            "likes": "story_time",
            "dislikes": "loud_crowds",
            "favorite_categories": "animals, play",
            "avoid_categories": "sports",
            "nap_time": "13:00-15:00",
            "bedtime": "19:30",
            "budget": "25",
            "max_drive": "35",
            "predefined_sources": ["baton-rouge-brec", "lafayette-library"],
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    database_url = client.app.state.db.database_url
    created = run_database_method(database_url, "get_user_by_email", "new@example.com")
    assert created is not None
    assert created.onboarding_complete is True
    assert created.home_city == "Baton Rouge"
    sources = run_database_method(database_url, "get_user_sources", created.id)
    assert len(sources) == 2


def test_signup_starts_scrape_tag_job(client, monkeypatch):
    import src.web.jobs as jobs_module
    from src.db.database import create_database
    from src.web.jobs import JobRegistry

    registry = JobRegistry()
    database_url = client.app.state.db.database_url
    monkeypatch.setattr(jobs_module, "Database", lambda: create_database(database_url=database_url))
    monkeypatch.setattr(jobs_module, "job_registry", registry)

    async def fake_run_scrape_then_tag(*args, **kwargs):
        progress_callback = kwargs.get("progress_callback")
        if progress_callback is not None:
            await progress_callback({"summary": "Running…"})
        await asyncio.sleep(0.2)
        return {
            "scraped": 1,
            "tagged": 1,
            "failed": 0,
            "summary": "1 events scraped · 1 tagged · 0 failed",
        }

    monkeypatch.setattr("src.scheduler.run_scrape_then_tag", fake_run_scrape_then_tag)

    page = client.get("/signup")
    csrf_token = extract_csrf_token(page.text)

    response = client.post(
        "/signup",
        data={
            "csrf_token": csrf_token,
            "email": "signup-job@example.com",
            "display_name": "New Parent",
            "password": "Password123",
            "confirm_password": "Password123",
            "home_city": "Baton Rouge",
            "preferred_cities": "Baton Rouge, Lafayette",
            "child_name": "Em",
            "temperament": "curious but sensitive to noise",
            "child_age_years": "3",
            "child_age_months": "6",
            "loves": "animals, music",
            "likes": "story_time",
            "dislikes": "loud_crowds",
            "favorite_categories": "animals, play",
            "avoid_categories": "sports",
            "nap_time": "13:00-15:00",
            "bedtime": "19:30",
            "budget": "25",
            "max_drive": "35",
            "predefined_sources": ["baton-rouge-brec"],
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    database_url = client.app.state.db.database_url
    created = run_database_method(database_url, "get_user_by_email", "signup-job@example.com")
    assert created is not None
    system_user = run_database_method(database_url, "get_user_by_email", SYSTEM_USER_EMAIL)
    assert system_user is not None
    jobs = run_database_method(database_url, "list_jobs", owner_user_id=system_user.id, limit=10)
    scrape_tag_jobs = [job for job in jobs if job.job_key == "pipeline:scrape-tag"]
    assert len(scrape_tag_jobs) == 1


def test_dashboard_and_profile_show_shared_initial_import_status(client, create_user):
    user = create_user(email="shared-import@example.com")
    login(client, email=user.email)
    database_url = client.app.state.db.database_url

    async def scenario() -> None:
        async with create_database(database_url=database_url) as db:
            system_user = await ensure_system_user(db)
            await db.create_job(
                Job(
                    kind="pipeline",
                    job_key="pipeline:scrape-tag",
                    label="Scrape + tag job",
                    owner_user_id=system_user.id,
                    state="running",
                    detail="Running…",
                    result_json='{"phase":"scrape","processed":0,"total":2,"summary":"Scraping sources…"}',
                )
            )

    from src.db.models import Job

    asyncio.run(scenario())

    dashboard = client.get("/")
    profile = client.get("/profile")
    shared_jobs = client.get("/jobs?scope=shared&kind=pipeline")

    assert dashboard.status_code == 200
    assert "Initial event import is running" in dashboard.text
    assert "/jobs?scope=shared" in dashboard.text
    assert "kind=pipeline" in dashboard.text
    assert profile.status_code == 200
    assert "Initial event import is running" in profile.text
    assert shared_jobs.status_code == 200
    assert "Shared Pipeline History" in shared_jobs.text
    assert "Scrape + tag job" in shared_jobs.text


def test_profile_notifications_requires_login(client):
    response = client.post(
        "/api/profile/notifications",
        data={"channels": ["console"], "child_name": "Em"},
    )

    assert response.status_code == 401
    assert "Please log in first" in response.headers.get("hx-trigger", "")


def test_profile_onboarding_updates_child_preferences(client, create_user):
    user = create_user(email="profile-update@example.com", home_city="Lafayette")
    login(client, email=user.email)
    profile = client.get("/profile")
    csrf_token = extract_csrf_token(profile.text)

    response = client.post(
        "/api/profile/onboarding",
        data={
            "csrf_token": csrf_token,
            "home_city": "Baton Rouge",
            "preferred_cities": "Lafayette, Baton Rouge, Lafayette",
            "child_name": "Em",
            "child_age_years": "4",
            "child_age_months": "2",
            "temperament": "curious but warms up slowly",
            "loves": "animals, music",
            "likes": "story time, splash pads",
            "dislikes": "loud crowds",
            "favorite_categories": "animals, play",
            "avoid_categories": "sports",
            "budget": "18",
            "max_drive": "25",
            "nap_time": "12:30-14:00",
            "bedtime": "19:45",
            "notes_for_recommendations": "Prefers calm mornings",
        },
    )

    assert response.status_code == 200
    assert "Child profile updated" in response.headers.get("hx-trigger", "")
    updated = run_database_method(client.app.state.db.database_url, "get_user_by_email", user.email)
    assert updated is not None
    assert updated.onboarding_complete is True
    assert updated.home_city == "Baton Rouge"
    assert updated.preferred_cities == ["Lafayette", "Baton Rouge"]
    assert updated.child_name == "Em"
    assert updated.interest_profile.child_age_years == 4
    assert updated.interest_profile.child_age_months == 2
    assert updated.interest_profile.constraints.home_city == "Baton Rouge"
    assert updated.interest_profile.constraints.preferred_cities == ["Lafayette", "Baton Rouge"]
    assert updated.interest_profile.constraints.max_drive_time_minutes == 25
    assert updated.interest_profile.constraints.budget_per_event == 18.0


def test_profile_password_change_updates_credentials(client, create_user):
    user = create_user(email="password-update@example.com")
    login(client, email=user.email)
    profile = client.get("/profile")
    csrf_token = extract_csrf_token(profile.text)

    response = client.post(
        "/api/profile/password",
        data={
            "csrf_token": csrf_token,
            "current_password": "Password123",
            "new_password": "Password456",
            "confirm_password": "Password456",
        },
    )

    assert response.status_code == 200
    assert "Password changed" in response.headers.get("hx-trigger", "")
    updated = run_database_method(client.app.state.db.database_url, "get_user_by_email", user.email)
    assert updated is not None
    assert updated.password_hash != user.password_hash
    assert verify_password("Password456", updated.password_hash)
    assert not verify_password("Password123", updated.password_hash)


def test_signup_duplicate_email_shows_inline_error_for_htmx(client, create_user):
    create_user(email="new@example.com")
    page = client.get("/signup")
    csrf_token = extract_csrf_token(page.text)

    response = client.post(
        "/signup",
        data={
            "csrf_token": csrf_token,
            "email": "new@example.com",
            "display_name": "New Parent",
            "password": "Password123",
            "confirm_password": "Password123",
            "home_city": "Baton Rouge",
            "preferred_cities": "Baton Rouge",
            "child_name": "Em",
            "temperament": "curious but sensitive to noise",
            "child_age_years": "3",
            "child_age_months": "6",
            "loves": "animals, music",
            "likes": "story_time",
            "dislikes": "loud_crowds",
            "favorite_categories": "animals, play",
            "avoid_categories": "sports",
            "nap_time": "13:00-15:00",
            "bedtime": "19:30",
            "budget": "25",
            "max_drive": "35",
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "An account with this email already exists. Log in instead." in response.text
    assert 'href="/login?email=new%40example.com"' in response.text
    login_page = client.get("/login?email=new@example.com")
    assert 'value="new@example.com"' in login_page.text


def test_signup_succeeds_on_local_http_loopback(
    isolated_postgres_database_url: str,
    monkeypatch,
):
    from src.web import app as appmod

    test_db = create_database(database_url=isolated_postgres_database_url)
    monkeypatch.setattr(appmod, "db", test_db)
    appmod.app.state.db = test_db
    appmod._rate_limit_store.clear()
    appmod._bulk_unattend_undo_store.clear()
    appmod.settings.app_base_url = "http://localhost:8000"

    with TestClient(appmod.app, base_url="http://127.0.0.1:8000") as local_client:
        page = local_client.get("/signup")
        assert "secure" not in page.headers.get("set-cookie", "").lower()
        csrf_token = extract_csrf_token(page.text)

        response = local_client.post(
            "/signup",
            data={
                "csrf_token": csrf_token,
                "email": "local@example.com",
                "display_name": "Local Parent",
                "password": "Password123",
                "confirm_password": "Password123",
                "home_city": "Lafayette",
                "preferred_cities": "Lafayette",
                "child_name": "Em",
                "temperament": "curious but sensitive to noise",
                "child_age_years": "3",
                "child_age_months": "0",
                "loves": "animals, music",
                "likes": "story_time",
                "dislikes": "loud_crowds",
                "favorite_categories": "animals, play",
                "avoid_categories": "sports",
                "nap_time": "13:00-15:00",
                "bedtime": "19:30",
                "budget": "25",
                "max_drive": "35",
            },
            headers={"Origin": "http://localhost:8000"},
            follow_redirects=False,
        )

    assert response.status_code == 302


def test_toggle_source_returns_refresh_trigger(client, create_user):
    user = create_user(email="toggle@example.com")

    source = Source(
        name="Example",
        url="https://example.com/events",
        domain=extract_domain("https://example.com/events"),
        user_id=user.id,
        status="active",
    )
    run_database_method(client.app.state.db.database_url, "create_source", source)

    login(client, email=user.email)
    page = client.get("/sources")
    csrf_token = extract_csrf_token(page.text)

    response = client.post(f"/api/sources/{source.id}/toggle", data={"csrf_token": csrf_token})

    assert response.status_code == 200
    assert "Disabled" in response.text
    assert "Enable" in response.text
    updated = run_database_method(client.app.state.db.database_url, "get_source", source.id)
    assert updated is not None
    assert updated.enabled is False


def test_delete_source_returns_refresh_trigger(client, create_user):
    user = create_user(email="delete@example.com")

    source = Source(
        name="Example",
        url="https://example.com/events",
        domain=extract_domain("https://example.com/events"),
        user_id=user.id,
        status="active",
    )
    run_database_method(client.app.state.db.database_url, "create_source", source)

    login(client, email=user.email)
    page = client.get("/sources")
    csrf_token = extract_csrf_token(page.text)

    response = client.request(
        "DELETE", f"/api/sources/{source.id}", data={"csrf_token": csrf_token}
    )

    assert response.status_code == 200
    assert response.text == ""
    deleted = run_database_method(client.app.state.db.database_url, "get_source", source.id)
    assert deleted is None


def test_attend_returns_updated_attendance_partial(client, create_user):
    user = create_user(email="attend@example.com")
    login(client, email=user.email)

    event = Event(
        source="test",
        source_url="https://example.com/event",
        source_id="evt-1",
        title="Story Time",
        location_city="Austin",
        start_time=datetime.now(tz=UTC),
    )
    run_database_method(client.app.state.db.database_url, "upsert_event", event)
    page = client.get(f"/event/{event.id}")
    csrf_token = extract_csrf_token(page.text)

    response = client.post(
        f"/api/attend/{event.id}?target_id=event-attendance",
        data={"csrf_token": csrf_token},
    )

    assert response.status_code == 200
    assert "Attended" in response.text
    assert "Undo" in response.text
    updated = run_database_method(
        client.app.state.db.database_url,
        "get_event",
        event.id,
        viewer_user_id=user.id,
    )
    assert updated is not None
    assert updated.viewer_state is not None
    assert updated.viewer_state.attended is True


def test_unattend_bulk_undo_stays_reactive(client, create_user):
    user = create_user(email="bulk@example.com")
    login(client, email=user.email)

    event = Event(
        source="test",
        source_url="https://example.com/event",
        source_id="evt-bulk",
        title="Museum Day",
        location_city="Austin",
        start_time=datetime.now(tz=UTC),
    )
    run_database_method(client.app.state.db.database_url, "upsert_event", event)
    run_database_method(client.app.state.db.database_url, "set_event_attended", user.id, event.id, True)
    page = client.get("/events?attended=yes")
    csrf_token = extract_csrf_token(page.text)

    response = client.post(
        "/api/unattend-bulk",
        data={"csrf_token": csrf_token, "event_ids": [event.id]},
    )

    assert response.status_code == 200
    trigger = response.headers.get("HX-Trigger", "")
    assert "Undo" in trigger
    updated = run_database_method(
        client.app.state.db.database_url,
        "get_event",
        event.id,
        viewer_user_id=user.id,
    )
    assert updated is not None
    assert updated.viewer_state is not None
    assert updated.viewer_state.attended is False


def test_attendance_is_user_scoped(client, create_user):
    user_a = create_user(email="user-a@example.com", home_city="Austin")
    user_b = create_user(email="user-b@example.com", home_city="Austin")

    event = Event(
        source="test",
        source_url="https://example.com/shared-event",
        source_id="evt-shared",
        title="Shared Event",
        location_city="Austin",
        start_time=datetime.now(tz=UTC),
    )
    run_database_method(client.app.state.db.database_url, "upsert_event", event)
    run_database_method(
        client.app.state.db.database_url,
        "set_event_attended",
        user_a.id,
        event.id,
        True,
    )

    viewed_by_a = run_database_method(
        client.app.state.db.database_url,
        "get_event",
        event.id,
        viewer_user_id=user_a.id,
    )
    viewed_by_b = run_database_method(
        client.app.state.db.database_url,
        "get_event",
        event.id,
        viewer_user_id=user_b.id,
    )

    assert viewed_by_a is not None and viewed_by_a.viewer_state is not None
    assert viewed_by_a.viewer_state.attended is True
    assert viewed_by_b is not None
    assert viewed_by_b.viewer_state is None or viewed_by_b.viewer_state.attended is False


def test_weekend_page_does_not_fall_back_to_recent_events_when_weekend_empty(client):
    response = client.get("/weekend")

    assert response.status_code == 200
    assert "No weekend events yet" in response.text or "still need tagging" in response.text
    assert "Top 3 Picks for Your Weekend" not in response.text


def test_weekend_page_tolerates_blank_legacy_nap_time(client, create_user):
    user = create_user(email="legacy@example.com")
    run_database_method(
        client.app.state.db.database_url,
        "update_user",
        user.id,
        interest_profile={
            **user.interest_profile.model_dump(),
            "constraints": {
                **user.interest_profile.constraints.model_dump(),
                "nap_time": "",
            },
        },
    )

    login(client, email=user.email)
    response = client.get("/weekend")

    assert response.status_code == 200


def test_duplicate_pipeline_request_reuses_existing_job_card(client, create_user, monkeypatch):
    import src.web.jobs as jobs_module
    from src.db.database import create_database
    from src.web.jobs import JobRegistry

    user = create_user(email="jobs@example.com")
    login(client, email=user.email)

    registry = JobRegistry()
    database_url = client.app.state.db.database_url
    monkeypatch.setattr(jobs_module, "Database", lambda: create_database(database_url=database_url))
    monkeypatch.setattr(jobs_module, "job_registry", registry)
    monkeypatch.setattr("src.web.jobs_ui.job_registry", registry)
    monkeypatch.setattr("src.web.routes.jobs.job_registry", registry)

    async def fake_scrape_then_tag(*args, **kwargs):
        progress_callback = kwargs.get("progress_callback")
        if progress_callback is not None:
            await progress_callback({"summary": "Running…"})
        await asyncio.sleep(0.2)
        return {
            "scraped": 1,
            "tagged": 1,
            "failed": 0,
            "summary": "1 events scraped · 1 tagged · 0 failed",
        }

    monkeypatch.setattr("src.scheduler.run_scrape_then_tag", fake_scrape_then_tag)

    page = client.get("/")
    csrf_token = extract_csrf_token(page.text)

    first = client.post("/api/scrape-tag", data={"csrf_token": csrf_token})
    second = client.post("/api/scrape-tag", data={"csrf_token": csrf_token})

    assert first.status_code == 200
    assert second.status_code == 200
    assert "started in the background" in first.headers.get("hx-trigger", "")
    assert "already running" in second.headers.get("hx-trigger", "")

    jobs = run_database_method(client.app.state.db.database_url, "list_jobs", owner_user_id=user.id, limit=10)
    running_jobs = [job for job in jobs if job.job_key == "pipeline:scrape-tag"]
    assert len(running_jobs) == 1
