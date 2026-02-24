"""Source router: maps URL domains to scraper implementations."""

from __future__ import annotations

from urllib.parse import urlparse

from .allevents import AllEventsScraper
from .base import BaseScraper
from .brec import BrecScraper
from .eventbrite import EventbriteScraper
from .lafayette import LafayetteScraper
from .library import LibraryScraper

# Domain (sans www.) → built-in scraper class
BUILTIN_DOMAINS: dict[str, type[BaseScraper]] = {
    "brec.org": BrecScraper,
    "eventbrite.com": EventbriteScraper,
    "allevents.in": AllEventsScraper,
    "moncuspark.org": LafayetteScraper,
    "acadianacenterforthearts.org": LafayetteScraper,
    "lafayettesciencemuseum.org": LafayetteScraper,
    "lafayettela.libcal.com": LibraryScraper,
    "ebrpl.libcal.com": LibraryScraper,
}


def extract_domain(url: str) -> str:
    """Extract the registrable domain from a URL.

    'https://www.brec.org/calendar' → 'brec.org'
    'https://lafayettela.libcal.com/rss.php' → 'lafayettela.libcal.com'
    """
    host = urlparse(url).hostname or ""
    # Strip leading 'www.'
    if host.startswith("www."):
        host = host[4:]
    return host.lower()


def is_builtin_domain(url: str) -> bool:
    """Check if a URL matches a built-in scraper domain."""
    domain = extract_domain(url)
    return domain in BUILTIN_DOMAINS


def get_builtin_scraper(url: str) -> BaseScraper | None:
    """Return the built-in scraper for a URL, or None."""
    domain = extract_domain(url)
    cls = BUILTIN_DOMAINS.get(domain)
    return cls() if cls else None
