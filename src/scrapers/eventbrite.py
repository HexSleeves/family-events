"""Eventbrite scraper parameterized by source metadata."""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
from datetime import UTC, datetime
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from src.db.models import Event, Source

from .base import BaseScraper

_EVENTBRITE_PATH_RE = re.compile(r"/d/(?P<state>[a-z]{2})--(?P<city>[a-z0-9-]+)/")


class EventbriteScraper(BaseScraper):
    def __init__(self, source: Source) -> None:
        self.source = source
        self.city = source.city.strip()
        self.search_url = source.url
        match = _EVENTBRITE_PATH_RE.search(urlparse(source.url).path.lower())
        if not match:
            raise ValueError(f"Unsupported Eventbrite search URL: {source.url}")
        self.state_slug = match.group("state")
        self.city_slug = match.group("city")
        self.source_name = f"builtin:eventbrite:{self.state_slug}:{self.city_slug}"

    async def scrape(self) -> list[Event]:
        async with self._client() as client:
            resp = await client.get(self.search_url)
            resp.raise_for_status()

        html = resp.text
        events = self._extract_json_ld(html)
        if events:
            return events

        events = self._extract_server_data(html)
        if events:
            return events

        return self._parse_html_cards(html)

    def _extract_json_ld(self, html: str) -> list[Event]:
        soup = BeautifulSoup(html, "html.parser")
        events: list[Event] = []
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(script.string or "")
            except json.JSONDecodeError:
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") != "Event":
                    continue
                events.append(self._ld_to_event(item))
        return events

    def _ld_to_event(self, ld: dict) -> Event:
        title = ld.get("name", "Untitled")
        start = ld.get("startDate", "")
        end = ld.get("endDate")
        url = ld.get("url", "")
        description = ld.get("description", "")
        image = ld.get("image")
        if isinstance(image, list):
            image = image[0] if image else None

        location = ld.get("location", {})
        loc_name = location.get("name", "") if isinstance(location, dict) else ""
        address_obj = location.get("address", {}) if isinstance(location, dict) else {}
        loc_address = (
            address_obj.get("streetAddress", "") if isinstance(address_obj, dict) else str(address_obj)
        )

        offers = ld.get("offers", {})
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        price_str = offers.get("price", "") if isinstance(offers, dict) else ""
        is_free = str(price_str).lower() in ("", "0", "0.00", "free")
        price_val = None
        with contextlib.suppress(ValueError, TypeError):
            price_val = float(price_str)

        sid = hashlib.md5((url or title).encode()).hexdigest()

        return Event(
            source=self.source_name,
            source_url=url,
            source_id=sid,
            title=title.strip(),
            description=description[:2000].strip(),
            location_name=loc_name,
            location_address=loc_address,
            location_city=self.city,
            start_time=self._parse_dt(start),
            end_time=self._parse_dt(end) if end else None,
            is_free=is_free,
            price_min=price_val,
            image_url=image,
            raw_data=ld,
        )

    def _extract_server_data(self, html: str) -> list[Event]:
        match = re.search(r"window\.__SERVER_DATA__\s*=\s*({.+?});\s*</script>", html, re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            return []

        search_data = data.get("search_data", data.get("searchData", {}))
        event_list = search_data.get("events", {}).get("results", [])
        if not event_list:
            return []

        return [self._server_item_to_event(item) for item in event_list]

    def _server_item_to_event(self, item: dict) -> Event:
        title = item.get("name", "Untitled")
        url = item.get("url", "")
        start = item.get("start_date") or item.get("start", {}).get("local", "")
        end = item.get("end_date") or item.get("end", {}).get("local")
        image = item.get("image", {})
        if isinstance(image, dict):
            image = image.get("url")
        is_free = item.get("is_free", True)
        venue = item.get("primary_venue", {}) or {}
        loc_name = venue.get("name", "")
        address = venue.get("address", {})
        loc_address = address.get("localized_address_display", "") if isinstance(address, dict) else ""
        sid = str(item.get("id", hashlib.md5((url or title).encode()).hexdigest()))

        return Event(
            source=self.source_name,
            source_url=url,
            source_id=sid,
            title=title.strip(),
            location_name=loc_name,
            location_address=loc_address,
            location_city=self.city,
            start_time=self._parse_dt(start),
            end_time=self._parse_dt(end) if end else None,
            is_free=is_free,
            image_url=image,
            raw_data=item,
        )

    def _parse_html_cards(self, html: str) -> list[Event]:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select(
            "div.search-event-card-wrapper, "
            "article.eds-event-card, "
            "div[data-testid='search-event-card'], "
            "li.search-main-content__events-list-item, "
            "div.discover-search-desktop-card"
        )
        self.log(f"HTML fallback: {len(cards)} cards found.")
        return [self._card_to_event(card) for card in cards]

    def _card_to_event(self, card) -> Event:
        title_el = card.select_one("h2, h3, .event-card__title, [data-testid='event-name']")
        title = title_el.get_text(strip=True) if title_el else "Untitled"

        link_el = card.select_one("a[href]")
        url = link_el["href"] if link_el else self.search_url

        date_el = card.select_one("p[class*='date'], time, .event-card__date")
        date_text = date_el.get_text(strip=True) if date_el else ""

        loc_el = card.select_one(
            "p[class*='location'], .event-card__location, .card-text--truncated__one"
        )
        loc_text = loc_el.get_text(strip=True) if loc_el else ""

        price_el = card.select_one("p[class*='price'], .event-card__price")
        price_text = price_el.get_text(strip=True) if price_el else ""
        is_free = "free" in price_text.lower() if price_text else True

        img_el = card.select_one("img")
        image = img_el.get("src") if img_el else None

        sid = hashlib.md5((url or title).encode()).hexdigest()

        return Event(
            source=self.source_name,
            source_url=url,
            source_id=sid,
            title=title,
            location_name=loc_text,
            location_city=self.city,
            start_time=self._parse_dt(date_text) if date_text else datetime.now(tz=UTC),
            is_free=is_free,
            image_url=image,
        )

    @staticmethod
    def _parse_dt(raw: str) -> datetime:
        raw = raw.strip()
        for fmt in (
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%a, %b %d, %Y %I:%M %p",
            "%a, %b %d, %I:%M %p",
            "%B %d, %Y",
            "%b %d, %Y",
        ):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
        raise ValueError(f"Cannot parse datetime: {raw!r}")
