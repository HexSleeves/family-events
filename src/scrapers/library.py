"""Library scraper parameterized by LibCal source metadata."""

from __future__ import annotations

import contextlib
import hashlib
import re
from datetime import datetime
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from src.db.models import Event, Source

from .base import BaseScraper


class LibraryScraper(BaseScraper):
    def __init__(self, source: Source) -> None:
        self.source = source
        parsed = urlparse(source.url)
        self.base_url = f"{parsed.scheme}://{parsed.netloc}"
        self.city = source.city.strip()
        self.source_name = f"builtin:library:{parsed.netloc}"

    async def scrape(self) -> list[Event]:
        try:
            events = await self._scrape_rss()
            self.log(f"{self.source.name}: {len(events)} events from RSS")
            return events
        except Exception as exc:
            self.log(f"{self.source.name} RSS failed: {exc}")
            events = await self._scrape_calendar_html()
            self.log(f"{self.source.name}: {len(events)} events from HTML")
            return events

    async def _scrape_rss(self) -> list[Event]:
        async with self._client() as client:
            resp = await client.get(self.source.url)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "xml")
        items = soup.find_all("item")
        if not items:
            soup = BeautifulSoup(resp.text, "html.parser")
            items = soup.find_all("item")

        return [self._rss_item_to_event(item) for item in items]

    def _rss_item_to_event(self, item) -> Event:
        title = item.find("title").get_text(strip=True) if item.find("title") else "Untitled"
        link = item.find("link").get_text(strip=True) if item.find("link") else self.base_url
        desc_el = item.find("description")
        description = desc_el.get_text(strip=True) if desc_el else ""
        if "<" in description:
            description = BeautifulSoup(description, "html.parser").get_text(
                separator=" ", strip=True
            )

        pub_date = item.find("pubDate")
        start_time = datetime.now()
        if pub_date:
            start_time = self._parse_rss_date(pub_date.get_text(strip=True))

        match = re.search(r"/event/(\d+)", link)
        source_id = match.group(1) if match else hashlib.md5(f"{title}{link}".encode()).hexdigest()

        return Event(
            source=self.source_name,
            source_url=link,
            source_id=source_id,
            title=title,
            description=description[:2000],
            location_name=self.source.name,
            location_city=self.city,
            start_time=start_time,
            is_free=True,
            raw_data={"source_library": self.source.name},
        )

    async def _scrape_calendar_html(self) -> list[Event]:
        for url in (f"{self.base_url}/calendar", f"{self.base_url}/upcoming"):
            async with self._client() as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return self._parse_libcal_html(resp.text)
        return []

    def _parse_libcal_html(self, html: str) -> list[Event]:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select(".s-lc-eventcard")
        events: list[Event] = []

        for card in cards:
            title_el = card.select_one(".s-lc-eventcard-title a, h2 a")
            title = title_el.get_text(strip=True) if title_el else ""
            if not title:
                continue

            href = str(title_el.get("href") or "") if title_el else ""
            month = card.select_one(".s-lc-evt-date-m")
            day = card.select_one(".s-lc-evt-date-d")
            date_text = (
                f"{month.get_text(strip=True)} {day.get_text(strip=True)}" if month and day else ""
            )
            time_el = card.select_one(".s-lc-eventcard-heading-text")
            time_text = time_el.get_text(strip=True) if time_el else ""
            loc_els = card.select(".s-lc-eventcard-heading-text")
            location = loc_els[1].get_text(strip=True) if len(loc_els) > 1 else self.source.name
            desc_el = card.select_one(".s-lc-eventcard-description")
            description = desc_el.get_text(strip=True) if desc_el else ""
            start_time = self._parse_libcal_datetime(date_text, time_text)
            match = re.search(r"/event/(\d+)", href)
            source_id = (
                match.group(1) if match else hashlib.md5(f"{title}{date_text}".encode()).hexdigest()
            )

            events.append(
                Event(
                    source=self.source_name,
                    source_url=href or self.base_url,
                    source_id=source_id,
                    title=title,
                    description=description[:2000],
                    location_name=location,
                    location_city=self.city,
                    start_time=start_time,
                    is_free=True,
                    raw_data={"source_library": self.source.name},
                )
            )

        return events

    def _parse_libcal_datetime(self, date_text: str, time_text: str) -> datetime:
        year = datetime.now().year
        dt = datetime.now()
        with contextlib.suppress(ValueError):
            dt = datetime.strptime(f"{date_text} {year}", "%b %d %Y")

        match = re.search(r"(\d{1,2}):(\d{2})(am|pm)", time_text.lower())
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2))
            ampm = match.group(3)
            if ampm == "pm" and hour != 12:
                hour += 12
            if ampm == "am" and hour == 12:
                hour = 0
            dt = dt.replace(hour=hour, minute=minute)
        return dt

    @staticmethod
    def _parse_rss_date(date_str: str) -> datetime:
        for fmt in (
            "%a, %d %b %Y %H:%M:%S %Z",
            "%a, %d %b %Y %H:%M:%S %z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d",
        ):
            with contextlib.suppress(ValueError):
                return datetime.strptime(date_str, fmt)
        return datetime.now()
