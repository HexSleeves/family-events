"""Event scrapers for family-events."""

from .allevents import AllEventsScraper
from .base import BaseScraper
from .brec import BrecScraper
from .eventbrite import EventbriteScraper
from .lafayette import LafayetteScraper
from .library import LibraryScraper

ALL_SCRAPERS: list[type[BaseScraper]] = [
    LafayetteScraper,
    BrecScraper,
    EventbriteScraper,
    LibraryScraper,
    AllEventsScraper,
]

__all__ = [
    "ALL_SCRAPERS",
    "AllEventsScraper",
    "BaseScraper",
    "BrecScraper",
    "EventbriteScraper",
    "LafayetteScraper",
    "LibraryScraper",
]
