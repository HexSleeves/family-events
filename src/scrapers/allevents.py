"""AllEvents.in scraper for family events in Lafayette & Baton Rouge.

Targets:
- https://allevents.in/lafayette/family
- https://allevents.in/baton-rouge/family

AllEvents renders event cards in the HTML with structured data.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from bs4 import BeautifulSoup

from src.db.models import Event
from .base import BaseScraper

SEARCH_URLS: dict[str, str] = {
    "Lafayette": "https://allevents.in/lafayette/family",
    "Baton Rouge": "https://allevents.in/baton-rouge/family",
}


class AllEventsScraper(BaseScraper):
    source_name = "allevents"

    async def scrape(self) -> list[Event]:
        all_events: list[Event] = []
        for city, url in SEARCH_URLS.items():
            try:
                events = await self._scrape_city(city, url)
                self.log(f"{city}: {len(events)} events.")
                all_events.extend(events)
            except Exception as exc:
                self.log(f"{city} failed: {exc}")
        return all_events

    async def _scrape_city(self, city: str, url: str) -> list[Event]:
        async with self._client() as client:
            resp = await client.get(url)
            resp.raise_for_status()

        html = resp.text

        # Try JSON-LD first (AllEvents often embeds it)
        events = self._extract_json_ld(html, city)
        if events:
            return events

        # Fall back to HTML card parsing
        return self._parse_html_cards(html, city)

    # -- JSON-LD -------------------------------------------------------------

    def _extract_json_ld(self, html: str, city: str) -> list[Event]:
        soup = BeautifulSoup(html, "html.parser")
        events: list[Event] = []

        for script in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(script.string or "")
            except json.JSONDecodeError:
                continue

            items: list[dict] = []
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                if data.get("@type") == "Event":
                    items = [data]
                elif "itemListElement" in data:
                    items = [
                        el.get("item", el)
                        for el in data["itemListElement"]
                        if isinstance(el, dict)
                    ]

            for item in items:
                if item.get("@type") != "Event":
                    continue
                try:
                    events.append(self._ld_to_event(item, city))
                except Exception as exc:
                    self.log(f"JSON-LD error: {exc}")

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
        elif isinstance(image, dict):
            image = image.get("url")

        location = ld.get("location", {})
        if isinstance(location, dict):
            loc_name = location.get("name", "")
            address_obj = location.get("address", {})
            if isinstance(address_obj, dict):
                loc_address = address_obj.get("streetAddress", "")
            else:
                loc_address = str(address_obj)
        else:
            loc_name = str(location)
            loc_address = ""

        offers = ld.get("offers", {})
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        price_str = offers.get("price", "") if isinstance(offers, dict) else ""
        is_free = str(price_str).lower() in ("", "0", "0.00", "free")
        price_val = None
        try:
            price_val = float(price_str)
        except (ValueError, TypeError):
            pass

        sid = hashlib.md5(url.encode()).hexdigest() if url else hashlib.md5(f"{title}{start}".encode()).hexdigest()

        return Event(
            source=self.source_name,
            source_url=url,
            source_id=sid,
            title=title.strip(),
            description=description[:2000].strip(),
            location_name=loc_name,
            location_address=loc_address,
            location_city=city if city in ("Lafayette", "Baton Rouge") else "Other",
            start_time=_parse_dt(start),
            end_time=_parse_dt(end) if end else None,
            is_free=is_free,
            price_min=price_val,
            image_url=image,
            raw_data=ld,
        )

    # -- HTML cards ----------------------------------------------------------

    def _parse_html_cards(self, html: str, city: str) -> list[Event]:
        soup = BeautifulSoup(html, "html.parser")
        events: list[Event] = []

        # AllEvents.in uses various card structures
        cards = soup.select(
            ".event-card, .item.event, .event-item, "
            "div[itemtype*='Event'], a[class*='event'], "
            ".search-result, .listing-item"
        )
        self.log(f"HTML fallback ({city}): {len(cards)} cards.")

        for card in cards:
            try:
                events.append(self._card_to_event(card, city))
            except Exception as exc:
                self.log(f"Card error: {exc}")
        return events

    def _card_to_event(self, card, city: str) -> Event:
        # Title
        title_el = card.select_one("h3, h2, h4, .title, .event-title, a")
        title = title_el.get_text(strip=True) if title_el else card.get_text(strip=True)[:120]

        # Link
        link = card.get("href", "")
        if not link and title_el and title_el.name == "a":
            link = title_el.get("href", "")
        if link and not link.startswith("http"):
            link = f"https://allevents.in{link}"

        # Date
        date_el = card.select_one(".date, time, .event-date, [datetime], .start-date")
        date_text = ""
        if date_el:
            date_text = date_el.get("datetime", "") or date_el.get_text(strip=True)

        # Location
        loc_el = card.select_one(".location, .event-location, .venue, .place")
        loc_text = loc_el.get_text(strip=True) if loc_el else ""

        # Image
        img_el = card.select_one("img")
        image = None
        if img_el:
            image = img_el.get("data-src") or img_el.get("src")

        # Price
        price_el = card.select_one(".price, .event-price, .ticket-price")
        price_text = price_el.get_text(strip=True) if price_el else ""
        is_free = not price_text or "free" in price_text.lower()

        sid = hashlib.md5(f"{title}{date_text}{city}".encode()).hexdigest()

        return Event(
            source=self.source_name,
            source_url=link or SEARCH_URLS.get(city, ""),
            source_id=sid,
            title=title,
            location_name=loc_text,
            location_city=city if city in ("Lafayette", "Baton Rouge") else "Other",
            start_time=_parse_dt(date_text) if date_text else datetime.utcnow(),
            is_free=is_free,
            image_url=image,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_dt(raw: str) -> datetime:
    raw = raw.strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%Y",
        "%B %d, %Y %I:%M %p",
        "%B %d, %Y",
        "%b %d, %Y %I:%M %p",
        "%b %d, %Y",
        "%a, %b %d, %Y %I:%M %p",
        "%a, %b %d",
    ):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse datetime: {raw!r}")
