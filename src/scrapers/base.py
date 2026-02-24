"""Abstract base scraper for all event sources."""

from abc import ABC, abstractmethod

import httpx

from src.db.models import Event

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


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
        kwargs.setdefault("headers", DEFAULT_HEADERS)
        kwargs.setdefault("timeout", 30.0)
        kwargs.setdefault("follow_redirects", True)
        return httpx.AsyncClient(**kwargs)
