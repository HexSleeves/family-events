"""Event scrapers for family-events."""

from .allevents import AllEventsScraper
from .base import BaseScraper
from .brec import BrecScraper
from .eventbrite import EventbriteScraper
from .generic import GenericScraper
from .lafayette import LafayetteScraper
from .library import LibraryScraper
from .router import BUILTIN_DOMAINS, extract_domain, get_builtin_scraper, is_builtin_domain

__all__ = [
    "BUILTIN_DOMAINS",
    "AllEventsScraper",
    "BaseScraper",
    "BrecScraper",
    "EventbriteScraper",
    "GenericScraper",
    "LafayetteScraper",
    "LibraryScraper",
    "extract_domain",
    "get_builtin_scraper",
    "is_builtin_domain",
]
