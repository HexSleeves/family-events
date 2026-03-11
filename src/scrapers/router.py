"""Source router: maps source metadata to scraper implementations."""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlparse

from src.db.models import Source

from .allevents import AllEventsScraper
from .base import BaseScraper
from .brec import BrecScraper
from .eventbrite import EventbriteScraper
from .lafayette import LafayetteScraper
from .library import LibraryScraper

BUILTIN_DOMAIN_MESSAGE = (
    "We already have a predefined source for this site. Add it from the catalog instead."
)

BUILTIN_DOMAINS: dict[str, Callable[[Source], BaseScraper]] = {
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
    host = urlparse(url).hostname or ""
    if host.startswith("www."):
        host = host[4:]
    return host.lower()


def is_builtin_domain(url: str) -> bool:
    return extract_domain(url) in BUILTIN_DOMAINS


def get_builtin_scraper(source: Source) -> BaseScraper | None:
    domain = extract_domain(source.url)
    cls = BUILTIN_DOMAINS.get(domain)
    return cls(source) if cls else None
