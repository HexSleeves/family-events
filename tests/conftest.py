from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.database import create_database
from src.db.models import User
from src.web import app as appmod
from src.web.auth import hash_password
from tests.postgres_test_helpers import (
    bootstrap_postgres_test_database,
    reset_postgres_test_database,
    run_database_method,
)


@pytest.fixture(scope="session")
def postgres_test_database_url() -> str:
    return bootstrap_postgres_test_database()


@pytest.fixture
def isolated_postgres_database_url(postgres_test_database_url: str) -> str:
    asyncio.run(reset_postgres_test_database(postgres_test_database_url))
    return postgres_test_database_url


@pytest.fixture
def client(
    isolated_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    test_db = create_database(database_url=isolated_postgres_database_url)
    monkeypatch.setattr(appmod, "db", test_db)
    appmod.app.state.db = test_db
    appmod._rate_limit_store.clear()
    appmod._bulk_unattend_undo_store.clear()
    appmod.settings.app_base_url = "https://testserver"
    with TestClient(appmod.app, base_url="https://testserver") as test_client:
        yield test_client


@pytest.fixture
def create_user() -> Callable[..., User]:
    def _create_user(**overrides) -> User:
        password = overrides.pop("password", "Password123")
        user = User(
            email=overrides.pop("email", "parent@example.com"),
            display_name=overrides.pop("display_name", "Parent"),
            password_hash=hash_password(password),
            **overrides,
        )
        run_database_method(appmod.db.database_url, "create_user", user)
        return user

    return _create_user


def extract_csrf_token(html: str) -> str:
    match = re.search(r'name="csrf-token" content="([^"]+)"', html)
    assert match, "csrf token meta tag not found"
    return match.group(1)
