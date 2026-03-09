"""SQLAlchemy async engine/session helpers for the Postgres migration path."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config import settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine(database_url: str | None = None) -> AsyncEngine:
    global _engine, _sessionmaker
    resolved_url = database_url or settings.database_url
    if _engine is None or str(_engine.url) != resolved_url:
        _engine = create_async_engine(resolved_url, future=True)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker(database_url: str | None = None) -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        get_engine(database_url)
    assert _sessionmaker is not None
    return _sessionmaker
