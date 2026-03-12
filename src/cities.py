from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.db.models import User

_PUNCT_RE = re.compile(r"[^\w\s-]")
_WHITESPACE_RE = re.compile(r"\s+")
_HYPHEN_RE = re.compile(r"[-_]+")


def normalize_city_slug(city: str) -> str:
    text = unicodedata.normalize("NFKD", str(city or ""))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = _WHITESPACE_RE.sub(" ", text.strip())
    text = _PUNCT_RE.sub("", text.lower())
    text = _HYPHEN_RE.sub("-", text.replace(" ", "-"))
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "unknown"


def normalize_city_list(cities: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for city in cities:
        slug = normalize_city_slug(city)
        if slug == "unknown" or slug in seen:
            continue
        seen.add(slug)
        deduped.append(slug)
    return deduped


def user_visible_city_slugs(user: User | None) -> list[str]:
    if user is None:
        return []
    return normalize_city_list([user.home_city, *user.preferred_cities])
