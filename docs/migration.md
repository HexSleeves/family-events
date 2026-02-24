# Migration Guide: Generic Scraper with Smart Routing

This document specifies every file to create, modify, and the exact changes needed
to implement the generic scraper system described in
[design-generic-scraper.md](design-generic-scraper.md).

## Change Summary

| Action | File | Lines |
|--------|------|-------|
| **Create** | `src/scrapers/recipe.py` | ~80 |
| **Create** | `src/scrapers/router.py` | ~50 |
| **Create** | `src/scrapers/generic.py` | ~200 |
| **Create** | `src/scrapers/analyzer.py` | ~180 |
| **Create** | `src/web/templates/sources.html` | ~90 |
| **Create** | `src/web/templates/source_detail.html` | ~80 |
| **Create** | `src/web/templates/partials/_source_card.html` | ~30 |
| **Create** | `src/web/templates/partials/_source_test_results.html` | ~30 |
| **Create** | `src/web/templates/partials/_skeleton_sources.html` | ~25 |
| **Modify** | `src/db/models.py` | +30 |
| **Modify** | `src/db/database.py` | +120 |
| **Modify** | `src/scrapers/__init__.py` | +10 |
| **Modify** | `src/scheduler.py` | +35, ~10 changed |
| **Modify** | `src/web/app.py` | +100 |
| **Modify** | `src/web/templates/base.html` | +1 (nav link) |

---

## Phase 1: Foundation

### 1.1 Create `src/scrapers/recipe.py`

New file. Pydantic models for the scrape recipe format.

```python
"""Pydantic models for scrape recipes."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class FieldRule(BaseModel):
    """Rule for extracting a single field from an event container."""

    selector: str | None = None
    attr: str | None = None       # None = textContent, "href", "datetime", "src", etc.
    format: str = "human"         # "iso", "human", or a strptime pattern
    default: str = ""


class Pagination(BaseModel):
    next_selector: str | None = None
    max_pages: int = 3


class CSSFields(BaseModel):
    title: FieldRule
    description: FieldRule | None = None
    start_time: FieldRule
    end_time: FieldRule | None = None
    location: FieldRule | None = None
    url: FieldRule | None = None
    price: FieldRule | None = None
    image: FieldRule | None = None


class CSSStrategy(BaseModel):
    event_container: str
    fields: CSSFields
    pagination: Pagination = Field(default_factory=Pagination)


class JSONLDStrategy(BaseModel):
    event_type: str = "Event"


class ScrapeRecipe(BaseModel):
    version: int = 1
    strategy: Literal["css", "jsonld"]
    analyzed_at: datetime
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: str = ""
    css: CSSStrategy | None = None
    jsonld: JSONLDStrategy | None = None
```

### 1.2 Create `src/scrapers/router.py`

New file. Maps domains to built-in scrapers, exposes routing logic.

```python
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
```

### 1.3 Modify `src/db/models.py`

Add `Source` model at the end of file, after `InterestProfile`.

**Append after the `InterestProfile` class (after line 113):**

```python
# ---------------------------------------------------------------------------
# Source - a user-defined scraping source
# ---------------------------------------------------------------------------


class Source(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    url: str
    domain: str
    builtin: bool = False
    recipe_json: str | None = None   # JSON string of ScrapeRecipe
    enabled: bool = True
    status: Literal[
        "pending", "analyzing", "active", "stale", "failed", "disabled"
    ] = "pending"
    last_scraped_at: datetime | None = None
    last_event_count: int = 0
    last_error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
```

### 1.4 Modify `src/db/database.py`

Three changes:

#### 1.4a Add `sources` table DDL

After `_CREATE_EVENTS_TABLE` (line 44), add:

```python
_CREATE_SOURCES_TABLE = """
CREATE TABLE IF NOT EXISTS sources (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    url             TEXT NOT NULL UNIQUE,
    domain          TEXT NOT NULL,
    builtin         INTEGER NOT NULL DEFAULT 0,
    recipe_json     TEXT,
    enabled         INTEGER NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'pending',
    last_scraped_at TEXT,
    last_event_count INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
"""
```

#### 1.4b Execute table creation in `connect()`

In the `connect()` method, after `await self._db.execute(_CREATE_EVENTS_TABLE)` (line 112), add:

```python
        await self._db.execute(_CREATE_SOURCES_TABLE)
```

#### 1.4c Add import and CRUD methods

Add to the imports at top of file:

```python
from .models import Event, EventTags, Source
```

(Currently imports only `Event, EventTags`.)

Add these methods to the `Database` class, before `mark_attended`:

```python
    # ------------------------------------------------------------------
    # Sources CRUD
    # ------------------------------------------------------------------

    async def create_source(self, source: Source) -> str:
        """Insert a new source. Returns the source id."""
        await self.db.execute(
            """
            INSERT INTO sources (
                id, name, url, domain, builtin, recipe_json,
                enabled, status, last_scraped_at, last_event_count,
                last_error, created_at, updated_at
            ) VALUES (
                :id, :name, :url, :domain, :builtin, :recipe_json,
                :enabled, :status, :last_scraped_at, :last_event_count,
                :last_error, :created_at, :updated_at
            )
            """,
            {
                "id": source.id,
                "name": source.name,
                "url": source.url,
                "domain": source.domain,
                "builtin": int(source.builtin),
                "recipe_json": source.recipe_json,
                "enabled": int(source.enabled),
                "status": source.status,
                "last_scraped_at": (
                    source.last_scraped_at.isoformat() if source.last_scraped_at else None
                ),
                "last_event_count": source.last_event_count,
                "last_error": source.last_error,
                "created_at": source.created_at.isoformat(),
                "updated_at": source.updated_at.isoformat(),
            },
        )
        await self.db.commit()
        return source.id

    async def get_source(self, source_id: str) -> Source | None:
        """Get a single source by id."""
        async with self.db.execute(
            "SELECT * FROM sources WHERE id = :id", {"id": source_id}
        ) as cursor:
            row = await cursor.fetchone()
            return _row_to_source(row) if row else None

    async def get_source_by_url(self, url: str) -> Source | None:
        """Get a source by URL (for duplicate detection)."""
        async with self.db.execute(
            "SELECT * FROM sources WHERE url = :url", {"url": url}
        ) as cursor:
            row = await cursor.fetchone()
            return _row_to_source(row) if row else None

    async def get_all_sources(self) -> list[Source]:
        """Get all sources, ordered by created_at desc."""
        async with self.db.execute(
            "SELECT * FROM sources ORDER BY created_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [_row_to_source(r) for r in rows]

    async def get_enabled_sources(self) -> list[Source]:
        """Get enabled, non-builtin sources with recipes."""
        async with self.db.execute(
            """
            SELECT * FROM sources
            WHERE enabled = 1 AND builtin = 0
              AND status IN ('active', 'stale')
            ORDER BY created_at
            """
        ) as cursor:
            rows = await cursor.fetchall()
            return [_row_to_source(r) for r in rows]

    async def update_source_recipe(
        self, source_id: str, recipe_json: str, status: str = "active"
    ) -> None:
        """Save a generated recipe for a source."""
        now = datetime.now(tz=UTC).isoformat()
        await self.db.execute(
            """
            UPDATE sources
            SET recipe_json = :recipe_json, status = :status, updated_at = :now
            WHERE id = :id
            """,
            {"recipe_json": recipe_json, "status": status, "now": now, "id": source_id},
        )
        await self.db.commit()

    async def update_source_status(
        self,
        source_id: str,
        *,
        status: str | None = None,
        count: int | None = None,
        error: str | None = None,
    ) -> None:
        """Update scrape results on a source."""
        now = datetime.now(tz=UTC).isoformat()
        sets = ["updated_at = :now"]
        params: dict[str, Any] = {"now": now, "id": source_id}
        if status is not None:
            sets.append("status = :status")
            params["status"] = status
        if count is not None:
            sets.append("last_event_count = :count")
            sets.append("last_scraped_at = :now")
            sets.append("last_error = NULL")
            params["count"] = count
            if count == 0:
                sets.append("status = 'stale'")
            elif status is None:
                sets.append("status = 'active'")
        if error is not None:
            sets.append("last_error = :error")
            params["error"] = error
        sql = f"UPDATE sources SET {', '.join(sets)} WHERE id = :id"
        await self.db.execute(sql, params)
        await self.db.commit()

    async def toggle_source(self, source_id: str) -> bool:
        """Toggle enabled/disabled. Returns new enabled state."""
        now = datetime.now(tz=UTC).isoformat()
        await self.db.execute(
            """
            UPDATE sources
            SET enabled = CASE WHEN enabled = 1 THEN 0 ELSE 1 END,
                status = CASE WHEN enabled = 1 THEN 'disabled' ELSE 'active' END,
                updated_at = :now
            WHERE id = :id
            """,
            {"now": now, "id": source_id},
        )
        await self.db.commit()
        source = await self.get_source(source_id)
        return source.enabled if source else False

    async def delete_source(self, source_id: str) -> None:
        """Delete a source and all its events."""
        # Get the source to determine its event source prefix
        source = await self.get_source(source_id)
        if source and not source.builtin:
            await self.db.execute(
                "DELETE FROM events WHERE source = :source",
                {"source": f"custom:{source_id}"},
            )
        await self.db.execute(
            "DELETE FROM sources WHERE id = :id", {"id": source_id}
        )
        await self.db.commit()
```

Also add a module-level helper function near `_row_to_event`:

```python
def _row_to_source(row: aiosqlite.Row) -> Source:
    """Convert a database row to a Source model."""
    d = dict(row)
    d["builtin"] = bool(d["builtin"])
    d["enabled"] = bool(d["enabled"])
    d["last_scraped_at"] = (
        datetime.fromisoformat(str(d["last_scraped_at"]))
        if d["last_scraped_at"]
        else None
    )
    d["created_at"] = datetime.fromisoformat(str(d["created_at"]))
    d["updated_at"] = datetime.fromisoformat(str(d["updated_at"]))
    return Source.model_validate(d)
```

### 1.5 Modify `src/scrapers/__init__.py`

Add new exports. Replace entire file:

```python
"""Event scrapers for family-events."""

from .allevents import AllEventsScraper
from .base import BaseScraper
from .brec import BrecScraper
from .eventbrite import EventbriteScraper
from .generic import GenericScraper
from .lafayette import LafayetteScraper
from .library import LibraryScraper
from .router import BUILTIN_DOMAINS, extract_domain, get_builtin_scraper, is_builtin_domain

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
    "BUILTIN_DOMAINS",
    "EventbriteScraper",
    "GenericScraper",
    "LafayetteScraper",
    "LibraryScraper",
    "extract_domain",
    "get_builtin_scraper",
    "is_builtin_domain",
]
```

---

## Phase 2: Generic Scraper + Analyzer

### 2.1 Create `src/scrapers/generic.py`

New file. The replay engine for CSS and JSON-LD recipes.

```python
"""Generic scraper that replays a ScrapeRecipe against any URL."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag
from dateutil import parser as dateutil_parser

from src.db.models import Event

from .base import BaseScraper
from .recipe import FieldRule, ScrapeRecipe


class GenericScraper(BaseScraper):
    """Scrapes any URL using a pre-generated ScrapeRecipe."""

    source_name = "custom"

    def __init__(self, url: str, source_id: str, recipe: ScrapeRecipe) -> None:
        self.url = url
        self.source_id = source_id
        self.recipe = recipe
        self.source_name = f"custom:{source_id}"

    async def scrape(self) -> list[Event]:
        if self.recipe.strategy == "jsonld":
            return await self._scrape_jsonld()
        elif self.recipe.strategy == "css":
            return await self._scrape_css()
        raise ValueError(f"Unknown strategy: {self.recipe.strategy}")

    # -- JSON-LD strategy ---------------------------------------------------

    async def _scrape_jsonld(self) -> list[Event]:
        async with self._client() as client:
            resp = await client.get(self.url)
            resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        events: list[Event] = []
        target_type = self.recipe.jsonld.event_type if self.recipe.jsonld else "Event"
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except json.JSONDecodeError:
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") == target_type:
                    event = self._jsonld_to_event(item)
                    if event:
                        events.append(event)
        self.log(f"JSON-LD: {len(events)} events")
        return events

    def _jsonld_to_event(self, data: dict) -> Event | None:
        title = data.get("name", "").strip()
        start_raw = data.get("startDate")
        if not title or not start_raw:
            return None
        location = data.get("location", {})
        loc_name = ""
        loc_addr = ""
        if isinstance(location, dict):
            loc_name = location.get("name", "")
            addr = location.get("address", "")
            loc_addr = addr if isinstance(addr, str) else addr.get("streetAddress", "")
        return Event(
            source=self.source_name,
            source_url=data.get("url", self.url),
            source_id=self._make_id(title, start_raw),
            title=title,
            description=data.get("description", "")[:2000],
            location_name=loc_name,
            location_address=loc_addr,
            start_time=self._parse_dt(start_raw),
            end_time=self._parse_dt(data.get("endDate")) if data.get("endDate") else None,
            is_free="free" in json.dumps(data.get("offers", "")).lower(),
            image_url=data.get("image", [None])[0]
            if isinstance(data.get("image"), list)
            else data.get("image"),
        )

    # -- CSS strategy -------------------------------------------------------

    async def _scrape_css(self) -> list[Event]:
        assert self.recipe.css is not None
        all_events: list[Event] = []
        url: str | None = self.url
        pages = 0
        max_pages = self.recipe.css.pagination.max_pages

        async with self._client() as client:
            while url and pages < max_pages:
                resp = await client.get(url)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")
                containers = soup.select(self.recipe.css.event_container)

                for el in containers:
                    event = self._extract_from_container(el, url)
                    if event:
                        all_events.append(event)

                # Pagination
                next_sel = self.recipe.css.pagination.next_selector
                if next_sel:
                    link = soup.select_one(next_sel)
                    url = urljoin(self.url, link["href"]) if link and link.get("href") else None
                else:
                    url = None
                pages += 1

        self.log(f"CSS: {len(all_events)} events from {pages} page(s)")
        return all_events

    def _extract_from_container(self, el: Tag, page_url: str) -> Event | None:
        assert self.recipe.css is not None
        fields = self.recipe.css.fields
        title = self._field(el, fields.title)
        start_raw = self._field(el, fields.start_time)
        if not title or not start_raw:
            return None

        event_url = self._field(el, fields.url)
        if event_url and not event_url.startswith("http"):
            event_url = urljoin(page_url, event_url)

        price_text = self._field(el, fields.price) if fields.price else ""
        is_free = not price_text or "free" in price_text.lower()
        price_val = self._extract_price(price_text) if not is_free else None

        image_url = self._field(el, fields.image) if fields.image else None
        if image_url and not image_url.startswith("http"):
            image_url = urljoin(page_url, image_url)

        return Event(
            source=self.source_name,
            source_url=event_url or page_url,
            source_id=self._make_id(title, start_raw),
            title=title,
            description=self._field(el, fields.description) if fields.description else "",
            location_name=self._field(el, fields.location) if fields.location else "",
            start_time=self._parse_dt(start_raw),
            end_time=(
                self._parse_dt(self._field(el, fields.end_time))
                if fields.end_time and self._field(el, fields.end_time)
                else None
            ),
            is_free=is_free,
            price_min=price_val,
            image_url=image_url,
        )

    # -- Helpers ------------------------------------------------------------

    @staticmethod
    def _field(el: Tag, rule: FieldRule | None) -> str:
        if not rule or not rule.selector:
            return rule.default if rule else ""
        found = el.select_one(rule.selector)
        if not found:
            return rule.default
        if rule.attr:
            return str(found.get(rule.attr, rule.default))
        return found.get_text(strip=True)

    @staticmethod
    def _parse_dt(raw: str | None) -> datetime:
        if not raw:
            return datetime.now()
        try:
            return dateutil_parser.parse(raw)
        except (ValueError, OverflowError):
            return datetime.now()

    @staticmethod
    def _make_id(title: str, date_str: str) -> str:
        slug = f"{title}:{date_str}".lower().strip()
        return hashlib.md5(slug.encode()).hexdigest()[:16]

    @staticmethod
    def _extract_price(text: str) -> float | None:
        match = re.search(r"\$([\d.]+)", text)
        return float(match.group(1)) if match else None
```

### 2.2 Create `src/scrapers/analyzer.py`

New file. Fetches a URL, checks for JSON-LD, falls back to LLM recipe generation.

```python
"""LLM-powered page analyzer that generates ScrapeRecipes."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import httpx
from bs4 import BeautifulSoup, Comment
from openai import AsyncOpenAI

from src.config import settings

from .recipe import ScrapeRecipe

_STRIP_TAGS = {
    "script", "style", "nav", "footer", "header", "iframe",
    "noscript", "svg", "form", "button", "input", "select",
}
_STRIP_CLASSES = {
    "cookie", "banner", "advertisement", "ad-", "sidebar",
    "popup", "modal", "newsletter", "social", "share",
}
_MAX_CLEAN_CHARS = 24_000  # ~6K tokens

RECIPE_SCHEMA = """
{
  "strategy": "css",
  "confidence": <float 0-1>,
  "notes": "<brief description of page structure>",
  "css": {
    "event_container": "<CSS selector for each event wrapper>",
    "fields": {
      "title":       {"selector": "<CSS>", "attr": null},
      "description": {"selector": "<CSS>", "attr": null},
      "start_time":  {"selector": "<CSS>", "attr": "<datetime or null>", "format": "<iso or human>"},
      "end_time":    {"selector": "<CSS>", "attr": "<datetime or null>", "format": "<iso or human>"},
      "location":    {"selector": "<CSS>", "attr": null},
      "url":         {"selector": "<CSS>", "attr": "href"},
      "price":       {"selector": "<CSS>", "attr": null, "default": "Free"},
      "image":       {"selector": "<CSS>", "attr": "src"}
    },
    "pagination": {
      "next_selector": "<CSS or null>",
      "max_pages": 3
    }
  }
}
Set selector to null for any field not present on the page.
"""

SYSTEM_PROMPT = f"""You are an expert web scraper. Given HTML from an events page, \
return a JSON scraping recipe with CSS selectors to extract each event.

Return ONLY valid JSON matching this schema:
{RECIPE_SCHEMA}

Rules:
- event_container: the repeating element wrapping each single event
- Use specific CSS selectors (tag.class, tag[attr], etc.)
- For dates: prefer datetime attribute on <time> tags (format: "iso"), \
otherwise use text content (format: "human")
- Set fields to null if not present
- confidence: your confidence that these selectors will extract events correctly"""


class PageAnalyzer:
    """Analyzes a URL and generates a ScrapeRecipe."""

    async def analyze(self, url: str) -> ScrapeRecipe:
        """Fetch a URL and generate a scraping recipe.

        Checks for JSON-LD first (free). Falls back to LLM analysis.
        """
        html = await self._fetch(url)
        soup = BeautifulSoup(html, "html.parser")

        # Try JSON-LD first (no LLM needed)
        jsonld_recipe = self._check_jsonld(soup)
        if jsonld_recipe:
            return jsonld_recipe

        # Fall back to LLM analysis
        cleaned = self._clean_html(soup)
        recipe = await self._llm_analyze(url, cleaned)

        # Validate recipe against the HTML
        validated = self._validate(soup, recipe)
        return validated

    # -- JSON-LD detection --------------------------------------------------

    def _check_jsonld(self, soup: BeautifulSoup) -> ScrapeRecipe | None:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except json.JSONDecodeError:
                continue
            items = data if isinstance(data, list) else [data]
            events = [i for i in items if i.get("@type") == "Event"]
            if events:
                return ScrapeRecipe(
                    version=1,
                    strategy="jsonld",
                    analyzed_at=datetime.now(tz=UTC),
                    confidence=0.95,
                    notes=f"Found {len(events)} JSON-LD Event(s)",
                    jsonld={"event_type": "Event"},
                )
        return None

    # -- HTML Cleaning ------------------------------------------------------

    def _clean_html(self, soup: BeautifulSoup) -> str:
        # Remove unwanted tags
        for tag in soup.find_all(_STRIP_TAGS):
            tag.decompose()
        # Remove comments
        for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
            comment.extract()
        # Remove elements with ad/cookie classes
        for el in soup.find_all(class_=True):
            classes = " ".join(el.get("class", []))
            if any(c in classes.lower() for c in _STRIP_CLASSES):
                el.decompose()
        # Prefer <main> content
        main = soup.find("main")
        root = main if main else soup.body or soup
        html = str(root)
        # Collapse whitespace
        html = re.sub(r"\s+", " ", html)
        # Truncate
        return html[:_MAX_CLEAN_CHARS]

    # -- LLM Analysis -------------------------------------------------------

    async def _llm_analyze(self, url: str, cleaned_html: str) -> ScrapeRecipe:
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Page URL: {url}\n\nHTML:\n{cleaned_html}"},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        raw = json.loads(response.choices[0].message.content or "{}")
        raw["version"] = 1
        raw["analyzed_at"] = datetime.now(tz=UTC).isoformat()
        return ScrapeRecipe.model_validate(raw)

    # -- Validation ---------------------------------------------------------

    def _validate(self, soup: BeautifulSoup, recipe: ScrapeRecipe) -> ScrapeRecipe:
        if recipe.strategy != "css" or not recipe.css:
            return recipe
        containers = soup.select(recipe.css.event_container)
        if not containers:
            recipe.confidence = min(recipe.confidence, 0.2)
            recipe.notes += " [WARN: container selector matched 0 elements]"
            return recipe
        # Check that title selector finds content in at least one container
        found_title = False
        for el in containers[:5]:
            title_el = el.select_one(recipe.css.fields.title.selector or "")
            if title_el and title_el.get_text(strip=True):
                found_title = True
                break
        if not found_title:
            recipe.confidence = min(recipe.confidence, 0.3)
            recipe.notes += " [WARN: title selector found no text]"
        return recipe

    # -- Fetch --------------------------------------------------------------

    async def _fetch(self, url: str) -> str:
        async with httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=30.0,
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text
```

### 2.3 Add `python-dateutil` dependency

```bash
uv add python-dateutil
```

Used by `GenericScraper._parse_dt()` for robust date parsing.

---

## Phase 3: Scheduler Integration

### 3.1 Modify `src/scheduler.py`

Replace the `run_scrape` function. The changes:
1. Keep built-in scrapers running as before
2. After built-ins, query `sources` table and run `GenericScraper` for each
3. Track success/failure per source

**Replace the entire `run_scrape` function (lines 29–53) with:**

```python
async def run_scrape(db: Database | None = None) -> int:
    """Run all scrapers (built-in + user-defined) and store results."""
    own_db = db is None
    if own_db:
        db = Database()
        await db.connect()

    total = 0

    # 1. Built-in scrapers
    for scraper in ALL_SCRAPERS:
        try:
            print(f"\n{'=' * 40}")
            print(f"Scraping: {scraper.source_name}")
            print(f"{'=' * 40}")
            events = await scraper.scrape()
            for event in events:
                await db.upsert_event(event)
            total += len(events)
            print(f"  \u2713 {len(events)} events from {scraper.source_name}")
        except Exception as e:
            print(f"  \u2717 {scraper.source_name} error: {e}")

    # 2. User-defined sources
    sources = await db.get_enabled_sources()
    for source in sources:
        try:
            if not source.recipe_json:
                continue
            recipe = ScrapeRecipe.model_validate_json(source.recipe_json)
            scraper = GenericScraper(
                url=source.url,
                source_id=source.id,
                recipe=recipe,
            )
            print(f"\n{'=' * 40}")
            print(f"Scraping custom: {source.name}")
            print(f"{'=' * 40}")
            events = await scraper.scrape()
            for event in events:
                await db.upsert_event(event)
            await db.update_source_status(source.id, count=len(events))
            total += len(events)
            print(f"  \u2713 {len(events)} events from {source.name}")
        except Exception as e:
            print(f"  \u2717 {source.name} error: {e}")
            await db.update_source_status(source.id, error=str(e))

    if own_db:
        await db.close()

    print(f"\nTotal events upserted: {total}")
    return total
```

**Add imports at top of file:**

```python
from src.scrapers.generic import GenericScraper
from src.scrapers.recipe import ScrapeRecipe
```

---

## Phase 4: Web UI

### 4.1 Modify `src/web/templates/base.html`

**Add nav link (after line 112, the Weekend link):**

```html
                <a href="/sources" class="px-3 py-1.5 rounded-md bg-white/15 hover:bg-white/30 text-sm transition">📡 Sources</a>
```

### 4.2 Modify `src/web/app.py`

Add these imports at the top:

```python
from src.scrapers.analyzer import PageAnalyzer
from src.scrapers.recipe import ScrapeRecipe
from src.scrapers.router import extract_domain, is_builtin_domain
from src.db.models import Source
```

Add these routes before the `# ----- API Endpoints` section:

```python
# ----- Sources Pages -----


@app.get("/sources", response_class=HTMLResponse)
async def sources_page(request: Request):
    sources = await db.get_all_sources()
    # Built-in source stats (read-only display)
    builtin_stats = await db.get_filter_options()  # reuse: gives us source names
    return templates.TemplateResponse(
        "sources.html",
        {"request": request, "sources": sources, "builtin_stats": builtin_stats},
    )


@app.get("/source/{source_id}", response_class=HTMLResponse)
async def source_detail(request: Request, source_id: str):
    source = await db.get_source(source_id)
    if not source:
        return HTMLResponse("Source not found", status_code=404)
    # Get events from this source
    events_from_source, _ = await db.search_events(
        days=90, source=f"custom:{source_id}", per_page=10
    )
    recipe = None
    if source.recipe_json:
        recipe = ScrapeRecipe.model_validate_json(source.recipe_json)
    return templates.TemplateResponse(
        "source_detail.html",
        {"request": request, "source": source, "recipe": recipe, "events": events_from_source},
    )
```

Add these API routes in the API section:

```python
@app.post("/api/sources", response_class=HTMLResponse)
async def api_add_source(request: Request):
    form = await request.form()
    url = str(form.get("url", "")).strip()
    name = str(form.get("name", "")).strip()
    if not url:
        return HTMLResponse(
            '<div class="text-red-600 font-semibold">\u274c Please enter a URL</div>'
        )

    # Check for built-in domain
    if is_builtin_domain(url):
        return HTMLResponse(
            '<div class="text-blue-600 font-semibold">'
            '\u2705 We already have built-in support for this site!</div>'
        )

    # Check for duplicate
    existing = await db.get_source_by_url(url)
    if existing:
        return HTMLResponse(
            '<div class="text-orange-600 font-semibold">'
            '\u26a0\ufe0f This URL has already been added</div>'
        )

    # Create source
    domain = extract_domain(url)
    if not name:
        name = domain.replace(".", " ").title()
    source = Source(name=name, url=url, domain=domain, status="analyzing")
    await db.create_source(source)

    # Analyze in-line (for now; could be background task later)
    try:
        analyzer = PageAnalyzer()
        recipe = await analyzer.analyze(url)
        await db.update_source_recipe(
            source.id,
            recipe.model_dump_json(),
            status="active" if recipe.confidence >= 0.3 else "failed",
        )
        return HTMLResponse(
            f'<div class="text-green-600 font-semibold">'
            f'\u2705 Source added! Strategy: {recipe.strategy}, '
            f'confidence: {recipe.confidence:.0%}</div>'
            f'<script>setTimeout(()=>location.reload(),1000)</script>'
        )
    except Exception as e:
        await db.update_source_status(source.id, status="failed", error=str(e))
        return HTMLResponse(
            f'<div class="text-red-600 font-semibold">\u274c Analysis failed: {e}</div>'
        )


@app.post("/api/sources/{source_id}/analyze", response_class=HTMLResponse)
async def api_reanalyze(source_id: str):
    source = await db.get_source(source_id)
    if not source:
        return HTMLResponse("Not found", status_code=404)
    await db.update_source_status(source_id, status="analyzing")
    try:
        analyzer = PageAnalyzer()
        recipe = await analyzer.analyze(source.url)
        await db.update_source_recipe(
            source_id,
            recipe.model_dump_json(),
            status="active" if recipe.confidence >= 0.3 else "failed",
        )
        return HTMLResponse(
            f'<div class="text-green-600 font-semibold">'
            f'\u2705 Re-analyzed! Confidence: {recipe.confidence:.0%}</div>'
            f'<script>setTimeout(()=>location.reload(),1000)</script>'
        )
    except Exception as e:
        await db.update_source_status(source_id, status="failed", error=str(e))
        return HTMLResponse(
            f'<div class="text-red-600 font-semibold">\u274c Analysis failed: {e}</div>'
        )


@app.post("/api/sources/{source_id}/test", response_class=HTMLResponse)
async def api_test_source(request: Request, source_id: str):
    source = await db.get_source(source_id)
    if not source or not source.recipe_json:
        return HTMLResponse("No recipe to test", status_code=400)
    try:
        from src.scrapers.generic import GenericScraper

        recipe = ScrapeRecipe.model_validate_json(source.recipe_json)
        scraper = GenericScraper(url=source.url, source_id=source.id, recipe=recipe)
        events = await scraper.scrape()
        return templates.TemplateResponse(
            "partials/_source_test_results.html",
            {"request": request, "events": events, "count": len(events)},
        )
    except Exception as e:
        return HTMLResponse(
            f'<div class="text-red-600 font-semibold">\u274c Test failed: {e}</div>'
        )


@app.post("/api/sources/{source_id}/toggle", response_class=HTMLResponse)
async def api_toggle_source(source_id: str):
    enabled = await db.toggle_source(source_id)
    state = "enabled" if enabled else "disabled"
    icon = "\u2705" if enabled else "\u23f8\ufe0f"
    return HTMLResponse(
        f'<div class="text-green-600 font-semibold">{icon} Source {state}</div>'
        f'<script>setTimeout(()=>location.reload(),500)</script>'
    )


@app.delete("/api/sources/{source_id}", response_class=HTMLResponse)
async def api_delete_source(source_id: str):
    await db.delete_source(source_id)
    return HTMLResponse(
        '<div class="text-green-600 font-semibold">\u2705 Source deleted</div>'
        '<script>setTimeout(()=>location.href="/sources",500)</script>'
    )
```

### 4.3 Create `src/web/templates/sources.html`

```html
{% extends "base.html" %}
{% block title %}Sources{% endblock %}
{% block content %}

<div class="flex items-center justify-between mb-4">
    <h2 class="text-lg font-semibold">📡 Event Sources</h2>
</div>

{# Add source form #}
<form
    hx-post="/api/sources"
    hx-target="#add-result"
    hx-swap="innerHTML"
    hx-indicator="#add-spinner"
    class="bg-white rounded-xl p-4 shadow-sm mb-6 flex gap-3 items-end"
>
    <div class="flex-1">
        <label class="text-xs text-gray-500 font-medium mb-1 block">URL</label>
        <input type="url" name="url" required placeholder="https://example.com/events"
            class="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400" />
    </div>
    <div class="w-48">
        <label class="text-xs text-gray-500 font-medium mb-1 block">Name (optional)</label>
        <input type="text" name="name" placeholder="Auto-detected"
            class="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400" />
    </div>
    <button type="submit"
        class="inline-flex items-center gap-1 px-4 py-2 rounded-lg bg-indigo-500 hover:bg-indigo-600 text-white font-semibold text-sm cursor-pointer transition">
        + Add Source
        <span id="add-spinner" class="htmx-indicator"><span class="spinner"></span></span>
    </button>
</form>
<div id="add-result" class="mb-4"></div>

{# Custom sources #}
{% if sources %}
<h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">Custom Sources ({{ sources | length }})</h3>
{% for source in sources %}
    {% include "partials/_source_card.html" %}
{% endfor %}
{% else %}
<div class="bg-white rounded-xl p-8 shadow-sm text-center text-gray-400 mb-6">
    No custom sources yet. Add a URL above to get started.
</div>
{% endif %}

{# Built-in sources (read-only) #}
<h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3 mt-8">Built-in Sources</h3>
<div class="bg-white rounded-xl p-4 shadow-sm space-y-2 text-sm">
    <div class="flex items-center gap-2">🔧 <span class="font-medium">BREC Parks</span> <span class="text-gray-400">brec.org</span></div>
    <div class="flex items-center gap-2">🔧 <span class="font-medium">Eventbrite</span> <span class="text-gray-400">eventbrite.com</span></div>
    <div class="flex items-center gap-2">🔧 <span class="font-medium">AllEvents</span> <span class="text-gray-400">allevents.in</span></div>
    <div class="flex items-center gap-2">🔧 <span class="font-medium">Lafayette Venues</span> <span class="text-gray-400">moncuspark.org, acadianacenterforthearts.org, lafayettesciencemuseum.org</span></div>
    <div class="flex items-center gap-2">🔧 <span class="font-medium">Libraries</span> <span class="text-gray-400">lafayettela.libcal.com, ebrpl.libcal.com</span></div>
</div>

{% endblock %}
```

### 4.4 Create `src/web/templates/partials/_source_card.html`

```html
{% set status_badges = {
    "active": {"class": "bg-green-100 text-green-800", "icon": "✅"},
    "stale": {"class": "bg-orange-100 text-orange-800", "icon": "⚠️"},
    "failed": {"class": "bg-red-100 text-red-800", "icon": "❌"},
    "analyzing": {"class": "bg-blue-100 text-blue-800", "icon": "🔄"},
    "pending": {"class": "bg-gray-100 text-gray-700", "icon": "⏳"},
    "disabled": {"class": "bg-gray-100 text-gray-500", "icon": "⏸️"},
} %}
{% set badge = status_badges.get(source.status, status_badges["pending"]) %}
<div class="bg-white rounded-xl p-4 shadow-sm mb-3">
    <div class="flex items-start justify-between">
        <div>
            <div class="flex items-center gap-2">
                <a href="/source/{{ source.id }}" class="font-semibold text-gray-900 hover:text-indigo-600">{{ source.name }}</a>
                <span class="inline-block px-2 py-0.5 rounded-full text-xs font-semibold {{ badge.class }}">{{ badge.icon }} {{ source.status }}</span>
            </div>
            <div class="text-gray-400 text-xs mt-0.5">{{ source.url }}</div>
            <div class="text-gray-500 text-xs mt-1">
                {% if source.recipe_json %}
                    Strategy: {{ "JSON-LD" if '"jsonld"' in source.recipe_json else "CSS selectors" }}
                {% endif %}
                {% if source.last_event_count %}
                    · {{ source.last_event_count }} events
                {% endif %}
                {% if source.last_scraped_at %}
                    · Last: {{ source.last_scraped_at.strftime("%b %d, %-I:%M%p") }}
                {% endif %}
            </div>
            {% if source.last_error %}
            <div class="text-red-500 text-xs mt-1">Error: {{ source.last_error[:100] }}</div>
            {% endif %}
        </div>
        <div class="flex gap-1.5">
            <button hx-post="/api/sources/{{ source.id }}/test" hx-target="#test-{{ source.id }}" hx-swap="innerHTML" hx-indicator="this"
                class="px-2.5 py-1 rounded-lg border border-gray-300 text-xs text-gray-700 hover:bg-gray-100 cursor-pointer transition">
                🧪 Test <span class="htmx-indicator"><span class="spinner" style="border-color:rgba(0,0,0,.2);border-top-color:#333;width:10px;height:10px"></span></span>
            </button>
            {% if source.status in ("stale", "failed") %}
            <button hx-post="/api/sources/{{ source.id }}/analyze" hx-target="#test-{{ source.id }}" hx-swap="innerHTML" hx-indicator="this"
                class="px-2.5 py-1 rounded-lg border border-gray-300 text-xs text-gray-700 hover:bg-gray-100 cursor-pointer transition">
                🔄 Re-analyze <span class="htmx-indicator"><span class="spinner" style="border-color:rgba(0,0,0,.2);border-top-color:#333;width:10px;height:10px"></span></span>
            </button>
            {% endif %}
            <button hx-post="/api/sources/{{ source.id }}/toggle" hx-target="#test-{{ source.id }}" hx-swap="innerHTML"
                class="px-2.5 py-1 rounded-lg border border-gray-300 text-xs text-gray-700 hover:bg-gray-100 cursor-pointer transition">
                {{ "⏸️ Disable" if source.enabled else "▶️ Enable" }}
            </button>
            <button hx-delete="/api/sources/{{ source.id }}" hx-target="#test-{{ source.id }}" hx-swap="innerHTML" hx-confirm="Delete this source and all its events?"
                class="px-2.5 py-1 rounded-lg border border-red-300 text-xs text-red-600 hover:bg-red-50 cursor-pointer transition">
                🗑️
            </button>
        </div>
    </div>
    <div id="test-{{ source.id }}" class="mt-2"></div>
</div>
```

### 4.5 Create `src/web/templates/source_detail.html`

```html
{% extends "base.html" %}
{% block title %}{{ source.name }}{% endblock %}
{% block content %}

<div class="mb-4">
    <a href="/sources" class="text-sm text-indigo-600 hover:text-indigo-800">← All Sources</a>
</div>

<div class="bg-white rounded-xl p-5 mb-4 shadow-sm">
    <h2 class="text-xl font-bold text-gray-900">{{ source.name }}</h2>
    <div class="text-gray-500 text-sm mt-1">
        <a href="{{ source.url }}" target="_blank" class="text-indigo-600 hover:underline">{{ source.url }}</a>
    </div>
    <div class="flex gap-2 mt-3">
        <span class="inline-block px-2 py-0.5 rounded-full text-xs font-semibold
            {{ 'bg-green-100 text-green-800' if source.status == 'active' else
               'bg-orange-100 text-orange-800' if source.status == 'stale' else
               'bg-red-100 text-red-800' if source.status == 'failed' else
               'bg-gray-100 text-gray-700' }}">
            {{ source.status }}
        </span>
        {% if source.last_event_count %}
        <span class="text-sm text-gray-500">{{ source.last_event_count }} events</span>
        {% endif %}
        {% if source.last_scraped_at %}
        <span class="text-sm text-gray-500">Last scraped: {{ source.last_scraped_at.strftime("%b %d at %-I:%M%p") }}</span>
        {% endif %}
    </div>
    <div class="flex gap-2 mt-4">
        <button hx-post="/api/sources/{{ source.id }}/test" hx-target="#detail-result" hx-swap="innerHTML" hx-indicator="this"
            class="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-indigo-500 hover:bg-indigo-600 text-white text-sm font-semibold cursor-pointer transition">
            🧪 Test Scrape <span class="htmx-indicator"><span class="spinner"></span></span>
        </button>
        <button hx-post="/api/sources/{{ source.id }}/analyze" hx-target="#detail-result" hx-swap="innerHTML" hx-indicator="this"
            class="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg border border-gray-300 text-sm font-semibold text-gray-700 hover:bg-gray-100 cursor-pointer transition">
            🔄 Re-analyze <span class="htmx-indicator"><span class="spinner" style="border-color:rgba(0,0,0,.2);border-top-color:#333"></span></span>
        </button>
    </div>
    <div id="detail-result" class="mt-3"></div>
</div>

{% if recipe %}
<div class="bg-white rounded-xl p-5 mb-4 shadow-sm">
    <h3 class="font-semibold text-gray-900 mb-3">📝 Scraping Recipe</h3>
    <div class="grid grid-cols-2 gap-2 text-sm mb-3">
        <div><strong>Strategy:</strong> {{ recipe.strategy }}</div>
        <div><strong>Confidence:</strong> {{ "%.0f%%" | format(recipe.confidence * 100) }}</div>
        <div><strong>Analyzed:</strong> {{ recipe.analyzed_at.strftime("%b %d, %Y %-I:%M%p") }}</div>
        {% if recipe.css %}
        <div><strong>Container:</strong> <code class="bg-gray-100 px-1 rounded text-xs">{{ recipe.css.event_container }}</code></div>
        {% endif %}
    </div>
    {% if recipe.notes %}
    <div class="text-gray-500 text-sm">{{ recipe.notes }}</div>
    {% endif %}
    {% if recipe.css %}
    <details class="mt-3">
        <summary class="text-sm text-indigo-600 cursor-pointer">Show field selectors</summary>
        <pre class="bg-gray-900 text-gray-200 p-3 rounded-lg mt-2 text-xs overflow-x-auto">{{ recipe.css.model_dump_json(indent=2) }}</pre>
    </details>
    {% endif %}
</div>
{% endif %}

{% if events %}
<div class="bg-white rounded-xl p-5 shadow-sm">
    <h3 class="font-semibold text-gray-900 mb-3">Recent Events ({{ events | length }})</h3>
    <div class="space-y-2">
        {% for event in events %}
        <div class="flex items-center gap-3 py-2 border-b border-gray-100 last:border-0">
            <div class="text-xs text-gray-400 w-20">{{ event.start_time.strftime("%m/%d %a") }}</div>
            <a href="/event/{{ event.id }}" class="text-sm text-indigo-600 hover:underline flex-1">{{ event.title }}</a>
            {% if event.tags %}
            <span class="inline-block px-2 py-0.5 rounded-full text-xs font-semibold bg-green-100 text-green-800">{{ event.tags.toddler_score }}/10</span>
            {% endif %}
        </div>
        {% endfor %}
    </div>
</div>
{% endif %}

{% endblock %}
```

### 4.6 Create `src/web/templates/partials/_source_test_results.html`

```html
{# HTMX partial: test scrape results #}
{% if count == 0 %}
<div class="text-orange-600 text-sm font-semibold">⚠️ No events found. Recipe may need updating.</div>
{% else %}
<div class="text-green-600 text-sm font-semibold mb-2">✅ Found {{ count }} events:</div>
<div class="bg-gray-50 rounded-lg p-3 max-h-60 overflow-y-auto space-y-1">
    {% for event in events[:10] %}
    <div class="text-xs">
        <span class="text-gray-400">{{ event.start_time.strftime("%m/%d") }}</span>
        <span class="font-medium">{{ event.title[:80] }}</span>
        {% if event.location_name %}<span class="text-gray-400">· {{ event.location_name }}</span>{% endif %}
    </div>
    {% endfor %}
    {% if count > 10 %}
    <div class="text-xs text-gray-400">... and {{ count - 10 }} more</div>
    {% endif %}
</div>
{% endif %}
```

---

## Phase 5: Polish

### 5.1 Custom source name in events table

The events table shows `source` as a badge. Custom sources will show as
`custom:uuid` which is ugly. Add a Jinja2 filter or template logic to
map `custom:*` sources to their human name.

**In `src/web/app.py`, add a template global** (after `templates = ...`):

```python
async def _source_display_name(source_str: str) -> str:
    """Convert 'custom:uuid' to the source's human name."""
    if source_str.startswith("custom:"):
        source_id = source_str[7:]
        source = await db.get_source(source_id)
        return source.name if source else source_str
    return source_str

# Can't use async in Jinja2 filters easily, so pre-compute in routes instead.
# Or store source_name on the Event at scrape time.
```

**Simpler alternative:** Set `Event.source` to the human name (e.g., `"custom:Downtown Events"`) instead of `"custom:{uuid}"`. This avoids the lookup entirely but changes the dedup key semantics.

**Recommended approach:** Keep `source = f"custom:{source.id}"` for dedup integrity. In templates, strip the prefix:

```html
{# In _event_row.html, replace the source badge #}
{% set source_label = event.source.replace("custom:", "") if event.source.startswith("custom:") else event.source %}
<span class="...">{{ source_label[:20] }}</span>
```

### 5.2 Quality checks

After all changes:

```bash
uv run ruff format src/
uv run ruff check src/ --fix
uv run ty check
sudo systemctl restart family-events
```

---

## File Change Checklist

```
[ ] src/scrapers/recipe.py           NEW     Pydantic recipe models
[ ] src/scrapers/router.py           NEW     Domain→scraper routing
[ ] src/scrapers/generic.py          NEW     CSS + JSON-LD replay scraper
[ ] src/scrapers/analyzer.py         NEW     LLM recipe generator
[ ] src/scrapers/__init__.py         MODIFY  Add new exports
[ ] src/db/models.py                 MODIFY  Add Source model
[ ] src/db/database.py               MODIFY  Add sources table + CRUD
[ ] src/scheduler.py                 MODIFY  Include user sources in run_scrape
[ ] src/web/app.py                   MODIFY  Add source routes + API
[ ] src/web/templates/base.html      MODIFY  Add Sources nav link
[ ] src/web/templates/sources.html               NEW
[ ] src/web/templates/source_detail.html          NEW
[ ] src/web/templates/partials/_source_card.html   NEW
[ ] src/web/templates/partials/_source_test_results.html  NEW
[ ] pyproject.toml                   MODIFY  Add python-dateutil dep
[ ] ruff format + ruff check + ty check           VERIFY
```
