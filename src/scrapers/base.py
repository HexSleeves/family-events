"""Abstract base scraper for all event sources."""

from abc import ABC, abstractmethod

import httpx

from src.db.models import Event
from src.http import build_async_client


class BaseScraper(ABC):
    """Every scraper inherits from this and implements *scrape*."""

    source_name: str

    @abstractmethod
    async def scrape(self) -> list[Event]:
        """Scrape events from this source."""
        ...

    # -- helpers -------------------------------------------------------------

    def log(self, msg: str) -> None:
        print(f"[{self.source_name}] {msg}")

    def _client(self, **kwargs) -> httpx.AsyncClient:
        """Return a pre-configured httpx async client."""
        return build_async_client(service=self.source_name, **kwargs)
