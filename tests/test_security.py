from __future__ import annotations

import re
import socket

from src.scrapers.analyzer import validate_public_http_url


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
        },
    )

    assert response.status_code == 403
    assert "Security check failed" in response.headers.get("hx-trigger", "")
