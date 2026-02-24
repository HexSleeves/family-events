"""Generic scraper that replays a ScrapeRecipe against any URL."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any
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

    def _jsonld_to_event(self, data: dict[str, Any]) -> Event | None:
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
            image_url=(
                data.get("image", [None])[0]
                if isinstance(data.get("image"), list)
                else data.get("image")
            ),
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
                    url = (
                        urljoin(self.url, str(link["href"])) if link and link.get("href") else None
                    )
                else:
                    url = None
                pages += 1

        self.log(f"CSS: {len(all_events)} events from {pages} page(s)")
        return all_events

    def _extract_from_container(self, el: Tag, page_url: str) -> Event | None:
        assert self.recipe.css is not None
        fields = self.recipe.css.fields
        title = self._field(el, fields.title)
        start_raw = self._field(el, fields.start_time) if fields.start_time else ""
        if not title:
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
