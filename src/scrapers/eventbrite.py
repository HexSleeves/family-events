"""Eventbrite scraper for Lafayette + Baton Rouge family events.

Scrapes the public search pages:
- https://www.eventbrite.com/d/la--lafayette/family-events/
- https://www.eventbrite.com/d/la--baton-rouge/family-events/

Eventbrite embeds structured JSON-LD and/or a __SERVER_DATA__ blob in the
page.  We try to extract that first; fall back to HTML card parsing.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
from datetime import UTC, datetime

from bs4 import BeautifulSoup

from src.db.models import Event

from .base import BaseScraper

SEARCH_URLS: dict[str, str] = {
    "Lafayette": "https://www.eventbrite.com/d/la--lafayette/family-events/",
    "Baton Rouge": "https://www.eventbrite.com/d/la--baton-rouge/family-events/",
}


class EventbriteScraper(BaseScraper):
    source_name = "eventbrite"

    async def scrape(self) -> list[Event]:
        all_events: list[Event] = []
        for city, url in SEARCH_URLS.items():
            try:
                events = await self._scrape_city(city, url)
                self.log(f"{city}: found {len(events)} events.")
                all_events.extend(events)
            except Exception as exc:
                self.log(f"{city} scrape failed: {exc}")
        return all_events

    async def _scrape_city(self, city: str, url: str) -> list[Event]:
        async with self._client() as client:
            resp = await client.get(url)
            resp.raise_for_status()

        html = resp.text

        # --- Try JSON-LD first ----------------------------------------------
        events = self._extract_json_ld(html, city)
        if events:
            return events

        # --- Try __SERVER_DATA__ / window.__PRELOADED_STATE__ ---------------
        events = self._extract_server_data(html, city)
        if events:
            return events

        # --- Fallback: parse HTML cards -------------------------------------
        return self._parse_html_cards(html, city)

    # -- JSON-LD extraction --------------------------------------------------

    def _extract_json_ld(self, html: str, city: str) -> list[Event]:
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
                try:
                    events.append(self._ld_to_event(item, city))
                except Exception as exc:
                    self.log(f"JSON-LD parse error: {exc}")
        return events

    def _ld_to_event(self, ld: dict, city: str) -> Event:
        title = ld.get("name", "Untitled")
        start = ld.get("startDate", "")
        end = ld.get("endDate")
        url = ld.get("url", "")
        description = ld.get("description", "")
        image = ld.get("image")
        if isinstance(image, list):
            image = image[0] if image else None

        location = ld.get("location", {})
        loc_name = location.get("name", "")
        address_obj = location.get("address", {})
        loc_address = (
            address_obj.get("streetAddress", "")
            if isinstance(address_obj, dict)
            else str(address_obj)
        )

        offers = ld.get("offers", {})
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        price_str = offers.get("price", "")
        is_free = str(price_str) in ("", "0", "0.00", "Free")
        price_val = None
        with contextlib.suppress(ValueError, TypeError):
            price_val = float(price_str)

        sid = (
            hashlib.md5(url.encode()).hexdigest()
            if url
            else hashlib.md5(title.encode()).hexdigest()
        )

        return Event(
            source=self.source_name,
            source_url=url,
            source_id=sid,
            title=title.strip(),
            description=description[:2000].strip(),
            location_name=loc_name,
            location_address=loc_address,
            location_city=city if city in ("Lafayette", "Baton Rouge") else "Other",
            start_time=self._parse_dt(start),
            end_time=self._parse_dt(end) if end else None,
            is_free=is_free,
            price_min=price_val,
            image_url=image,
            raw_data=ld,
        )

    # -- __SERVER_DATA__ extraction ------------------------------------------

    def _extract_server_data(self, html: str, city: str) -> list[Event]:
        # Eventbrite sometimes embeds data in a window.__SERVER_DATA__ variable
        match = re.search(r"window\.__SERVER_DATA__\s*=\s*({.+?});\s*</script>", html, re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            return []

        # Navigate into the nested structure - path may change
        search_data = data.get("search_data", data.get("searchData", {}))
        event_list = search_data.get("events", {}).get("results", [])
        if not event_list:
            return []

        events: list[Event] = []
        for item in event_list:
            try:
                events.append(self._server_item_to_event(item, city))
            except Exception as exc:
                self.log(f"Server data parse error: {exc}")
        return events

    def _server_item_to_event(self, item: dict, city: str) -> Event:
        title = item.get("name", "Untitled")
        url = item.get("url", "")
        start = item.get("start_date") or item.get("start", {}).get("local", "")
        end = item.get("end_date") or item.get("end", {}).get("local")
        image = (
            item.get("image", {}).get("url")
            if isinstance(item.get("image"), dict)
            else item.get("image")
        )
        is_free = item.get("is_free", True)
        venue = item.get("primary_venue", {}) or {}
        loc_name = venue.get("name", "")
        address = venue.get("address", {})
        loc_address = (
            address.get("localized_address_display", "") if isinstance(address, dict) else ""
        )

        sid = str(item.get("id", hashlib.md5(url.encode()).hexdigest()))

        return Event(
            source=self.source_name,
            source_url=url,
            source_id=sid,
            title=title.strip(),
            location_name=loc_name,
            location_address=loc_address,
            location_city=city if city in ("Lafayette", "Baton Rouge") else "Other",
            start_time=self._parse_dt(start),
            end_time=self._parse_dt(end) if end else None,
            is_free=is_free,
            image_url=image,
            raw_data=item,
        )

    # -- HTML card fallback --------------------------------------------------

    def _parse_html_cards(self, html: str, city: str) -> list[Event]:
        soup = BeautifulSoup(html, "html.parser")
        events: list[Event] = []

        # Eventbrite uses various card selectors; try several
        cards = soup.select(
            "div.search-event-card-wrapper, "
            "article.eds-event-card, "
            "div[data-testid='search-event-card'], "
            "li.search-main-content__events-list-item, "
            "div.discover-search-desktop-card"
        )
        self.log(f"HTML fallback: {len(cards)} cards found.")

        for card in cards:
            try:
                events.append(self._card_to_event(card, city))
            except Exception as exc:
                self.log(f"Card parse error: {exc}")
        return events

    def _card_to_event(self, card, city: str) -> Event:
        # Title
        title_el = card.select_one("h2, h3, .event-card__title, [data-testid='event-name']")
        title = title_el.get_text(strip=True) if title_el else "Untitled"

        # Link
        link_el = card.select_one("a[href*='eventbrite.com/e/']")
        url = link_el["href"] if link_el else ""

        # Date
        date_el = card.select_one("p[class*='date'], time, .event-card__date")
        date_text = date_el.get_text(strip=True) if date_el else ""

        # Location
        loc_el = card.select_one(
            "p[class*='location'], .event-card__location, .card-text--truncated__one"
        )
        loc_text = loc_el.get_text(strip=True) if loc_el else ""

        # Price
        price_el = card.select_one("p[class*='price'], .event-card__price")
        price_text = price_el.get_text(strip=True) if price_el else ""
        is_free = "free" in price_text.lower() if price_text else True

        # Image
        img_el = card.select_one("img")
        image = img_el.get("src") if img_el else None

        sid = (
            hashlib.md5(url.encode()).hexdigest()
            if url
            else hashlib.md5(title.encode()).hexdigest()
        )

        return Event(
            source=self.source_name,
            source_url=url,
            source_id=sid,
            title=title,
            location_name=loc_text,
            location_city=city if city in ("Lafayette", "Baton Rouge") else "Other",
            start_time=self._parse_dt(date_text) if date_text else datetime.now(tz=UTC),
            is_free=is_free,
            image_url=image,
        )

    # -- utils ---------------------------------------------------------------

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
