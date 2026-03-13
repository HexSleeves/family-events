"""Abstract base scraper for all event sources."""

import logging
from abc import ABC, abstractmethod

import httpx

from src.db.models import Event
from src.http import build_async_client

logger = logging.getLogger("uvicorn.error")


class BaseScraper(ABC):
    """Every scraper inherits from this and implements *scrape*."""

    source_name: str

    @abstractmethod
    async def scrape(self) -> list[Event]:
        """Scrape events from this source."""
        ...

    # -- helpers -------------------------------------------------------------

    def log(self, msg: str, *, level: int = logging.INFO, **context: object) -> None:
        logger.log(
            level,
            "scraper_message",
            extra={
                "stage": "scrape",
                "source_name": self.source_name,
                "scraper_class": type(self).__name__,
                "detail": msg,
                **{key: value for key, value in context.items() if value is not None},
            },
        )

    def _client(self, **kwargs) -> httpx.AsyncClient:
        """Return a pre-configured httpx async client."""
        return build_async_client(service=self.source_name, **kwargs)
