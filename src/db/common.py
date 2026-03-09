"""Shared database-layer helper functions."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta

from src.db.models import Event

USER_UPDATE_FIELDS = frozenset(
    {
        "display_name",
        "home_city",
        "preferred_cities",
        "theme",
        "notification_channels",
        "email_to",
        "sms_to",
        "child_name",
        "onboarding_complete",
        "interest_profile",
        "password_hash",
    }
)


def canonicalize_title(title: str) -> str:
    """Normalize title text for fuzzy matching."""
    text = title.lower().strip()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def event_fingerprint(event: Event) -> str:
    """Build a stable cross-source fingerprint for likely duplicate events."""
    date_part = event.start_time.date().isoformat()
    city = (event.location_city or "").lower().strip()
    title = canonicalize_title(event.title)
    key = f"{title}|{date_part}|{city}"
    return hashlib.sha1(key.encode()).hexdigest()


def title_similarity(a: str, b: str) -> float:
    """Token overlap similarity for fuzzy title matching."""
    a_tokens = set(canonicalize_title(a).split())
    b_tokens = set(canonicalize_title(b).split())
    if not a_tokens or not b_tokens:
        return 0.0
    overlap = len(a_tokens & b_tokens)
    union = len(a_tokens | b_tokens)
    return overlap / union if union else 0.0


def normalize_email(email: str) -> str:
    """Normalize emails before lookups and persistence."""
    return email.lower().strip()


def normalize_search_query(query: str) -> str:
    """Normalize free-text filters before SQL binding."""
    return query.strip()


def time_window(days: int) -> tuple[datetime, datetime]:
    """Return a UTC time window from now through the next N days."""
    now = datetime.now(tz=UTC)
    return now, now + timedelta(days=days)
