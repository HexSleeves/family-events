"""Shared database-layer helper functions."""

from __future__ import annotations

import hashlib
import re

from src.db.models import Event


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
