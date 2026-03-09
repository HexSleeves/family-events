"""Postgres-backed database implementation scaffold."""

from __future__ import annotations

from typing import Any

from src.db.session import get_engine


class PostgresDatabase:
    """Placeholder Postgres implementation.

    Phase 1 of the refactor wires in engine lifecycle only. Query methods will be
    ported incrementally behind the same public API.
    """

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.engine = None

    async def connect(self) -> None:
        self.engine = get_engine(self.database_url)
        async with self.engine.connect() as conn:
            await conn.run_sync(lambda _sync_conn: None)

    async def close(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()
            self.engine = None

    async def __aenter__(self) -> PostgresDatabase:
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"Postgres backend method not implemented yet: {name}")
