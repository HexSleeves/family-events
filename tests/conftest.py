from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.database import Database
from src.db.models import User
from src.web import app as appmod
from src.web.auth import hash_password


@pytest.fixture
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    test_db = Database(str(tmp_path / "test.db"))
    monkeypatch.setattr(appmod, "db", test_db)
    appmod.app.state.db = test_db
    appmod._rate_limit_store.clear()
    appmod._bulk_unattend_undo_store.clear()
    appmod.settings.app_base_url = "https://testserver"
    with TestClient(appmod.app, base_url="https://testserver") as test_client:
        yield test_client


@pytest.fixture
def create_user() -> Callable[..., User]:
    def _create_user(**overrides: str) -> User:
        user = User(
            email=overrides.get("email", "parent@example.com"),
            display_name=overrides.get("display_name", "Parent"),
            password_hash=hash_password(overrides.get("password", "Password123")),
        )
        asyncio.run(appmod.db.create_user(user))
        return user

    return _create_user


def extract_csrf_token(html: str) -> str:
    match = re.search(r'name="csrf-token" content="([^"]+)"', html)
    assert match, "csrf token meta tag not found"
    return match.group(1)
