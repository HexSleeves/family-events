"""Event scrapers for family-events."""

from .base import BaseScraper
from .brec import BrecScraper
from .eventbrite import EventbriteScraper
from .lafayette_gov import LafayetteGovScraper
from .library import LibraryScraper
from .allevents import AllEventsScraper

ALL_SCRAPERS: list[type[BaseScraper]] = [
    BrecScraper,
    EventbriteScraper,
    LafayetteGovScraper,
    LibraryScraper,
    AllEventsScraper,
]

__all__ = [
    "BaseScraper",
    "BrecScraper",
    "EventbriteScraper",
    "LafayetteGovScraper",
    "LibraryScraper",
    "AllEventsScraper",
    "ALL_SCRAPERS",
]
