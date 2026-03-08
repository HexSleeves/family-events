from __future__ import annotations

import asyncio
import re
import socket

from src.db.models import Source
from src.scrapers.analyzer import validate_public_http_url
from src.scrapers.router import extract_domain


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
    user = client.app.state.db
    created = __import__("asyncio").run(user.get_user_by_email("new@example.com"))
    assert created is not None
    assert created.onboarding_complete is True
    assert created.home_city == "Baton Rouge"
    sources = __import__("asyncio").run(user.get_user_sources(created.id))
    assert len(sources) == 2


def test_toggle_source_returns_refresh_trigger(client, create_user):
    user = create_user(email="toggle@example.com")

    source = Source(
        name="Example",
        url="https://example.com/events",
        domain=extract_domain("https://example.com/events"),
        user_id=user.id,
        status="active",
    )
    asyncio.run(client.app.state.db.create_source(source))

    login(client, email=user.email)
    page = client.get("/sources")
    csrf_token = extract_csrf_token(page.text)

    response = client.post(f"/api/sources/{source.id}/toggle", data={"csrf_token": csrf_token})

    assert response.status_code == 200
    assert "Disabled" in response.text
    assert "Enable" in response.text
    updated = asyncio.run(client.app.state.db.get_source(source.id))
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
    asyncio.run(client.app.state.db.create_source(source))

    login(client, email=user.email)
    page = client.get("/sources")
    csrf_token = extract_csrf_token(page.text)

    response = client.request("DELETE", f"/api/sources/{source.id}", data={"csrf_token": csrf_token})

    assert response.status_code == 200
    assert response.text == ""
    deleted = asyncio.run(client.app.state.db.get_source(source.id))
    assert deleted is None
