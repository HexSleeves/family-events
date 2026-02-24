"""Library events scraper.

Both Lafayette Public Library (lafayettela.libcal.com) and
East Baton Rouge Parish Library (ebrpl.libcal.com) use Springshare LibCal.

LibCal renders events client-side, so we fetch the calendar page via the
browser (httpx with JS rendering won't work). Instead, we use their RSS/iCal
feeds or the LibCal widget API.

Actual approach: Fetch the grid/gallery view HTML which contains event cards
rendered via server-side include after JS hydration. Since we can't run JS
with plain httpx, we'll use the LibCal RSS feed:
  https://lafayettela.libcal.com/rss.php
  https://ebrpl.libcal.com/rss.php
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime

from bs4 import BeautifulSoup

from src.db.models import Event
from .base import BaseScraper

LIBRARY_SOURCES = [
    {
        "name": "Lafayette Public Library",
        "city": "Lafayette",
        "rss_url": "https://lafayettela.libcal.com/rss.php",
        "base_url": "https://lafayettela.libcal.com",
    },
    {
        "name": "East Baton Rouge Parish Library",
        "city": "Baton Rouge",
        "rss_url": "https://ebrpl.libcal.com/rss.php",
        "base_url": "https://ebrpl.libcal.com",
    },
]


class LibraryScraper(BaseScraper):
    source_name = "library"

    async def scrape(self) -> list[Event]:
        all_events: list[Event] = []
        for lib in LIBRARY_SOURCES:
            try:
                events = await self._scrape_rss(lib)
                self.log(f"{lib['name']}: {len(events)} events from RSS")
                all_events.extend(events)
            except Exception as exc:
                self.log(f"{lib['name']} RSS failed: {exc}")
                # Fallback: try fetching the calendar HTML directly
                try:
                    events = await self._scrape_calendar_html(lib)
                    self.log(f"{lib['name']}: {len(events)} events from HTML")
                    all_events.extend(events)
                except Exception as exc2:
                    self.log(f"{lib['name']} HTML also failed: {exc2}")
        return all_events

    async def _scrape_rss(self, lib: dict) -> list[Event]:
        """Parse the LibCal RSS feed."""
        async with self._client() as client:
            resp = await client.get(lib["rss_url"])
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "xml")
        items = soup.find_all("item")
        if not items:
            # Maybe it's HTML not XML
            soup = BeautifulSoup(resp.text, "html.parser")
            items = soup.find_all("item")

        events: list[Event] = []
        city = lib["city"]
        for item in items:
            try:
                events.append(self._rss_item_to_event(item, city, lib))
            except Exception as exc:
                self.log(f"RSS item error: {exc}")
        return events

    def _rss_item_to_event(self, item, city: str, lib: dict) -> Event:
        title = item.find("title").get_text(strip=True) if item.find("title") else "Untitled"
        link = item.find("link").get_text(strip=True) if item.find("link") else lib["base_url"]
        desc_el = item.find("description")
        description = desc_el.get_text(strip=True) if desc_el else ""
        # Strip HTML from description
        if "<" in description:
            description = BeautifulSoup(description, "html.parser").get_text(separator=" ", strip=True)

        pub_date = item.find("pubDate")
        start_time = datetime.now()
        if pub_date:
            start_time = self._parse_rss_date(pub_date.get_text(strip=True))

        # Extract event ID from URL like /event/15766217
        source_id = ""
        m = re.search(r"/event/(\d+)", link)
        if m:
            source_id = m.group(1)
        else:
            source_id = hashlib.md5(f"{title}{link}".encode()).hexdigest()

        return Event(
            source=self.source_name,
            source_url=link,
            source_id=source_id,
            title=title,
            description=description[:2000],
            location_name=lib["name"],
            location_city=city if city in ("Lafayette", "Baton Rouge") else "Other",
            start_time=start_time,
            is_free=True,
            raw_data={"source_library": lib["name"]},
        )

    async def _scrape_calendar_html(self, lib: dict) -> list[Event]:
        """Fallback: scrape the main library website for event listings."""
        # Try common library website event pages
        urls_to_try = [
            f"{lib['base_url']}/calendar",
            f"{lib['base_url']}/upcoming",
        ]
        for url in urls_to_try:
            try:
                async with self._client() as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        return self._parse_libcal_html(resp.text, lib)
            except Exception:
                continue
        return []

    def _parse_libcal_html(self, html: str, lib: dict) -> list[Event]:
        """Parse LibCal event cards from HTML (if server-rendered)."""
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select(".s-lc-eventcard")
        events: list[Event] = []
        city = lib["city"]

        for card in cards:
            try:
                # Title
                title_el = card.select_one(".s-lc-eventcard-title a, h2 a")
                title = title_el.get_text(strip=True) if title_el else ""
                if not title:
                    continue

                # Link
                href = title_el.get("href", "") if title_el else ""

                # Date
                month = card.select_one(".s-lc-evt-date-m")
                day = card.select_one(".s-lc-evt-date-d")
                date_text = f"{month.get_text(strip=True)} {day.get_text(strip=True)}" if month and day else ""

                # Time
                time_el = card.select_one(".s-lc-eventcard-heading-text")
                time_text = time_el.get_text(strip=True) if time_el else ""

                # Location (second heading text)
                loc_els = card.select(".s-lc-eventcard-heading-text")
                location = loc_els[1].get_text(strip=True) if len(loc_els) > 1 else lib["name"]

                # Description
                desc_el = card.select_one(".s-lc-eventcard-description")
                description = desc_el.get_text(strip=True) if desc_el else ""

                # Parse datetime
                start_time = self._parse_libcal_datetime(date_text, time_text)

                # Source ID
                m = re.search(r"/event/(\d+)", href)
                source_id = m.group(1) if m else hashlib.md5(f"{title}{date_text}".encode()).hexdigest()

                events.append(Event(
                    source=self.source_name,
                    source_url=href or lib["base_url"],
                    source_id=source_id,
                    title=title,
                    description=description[:2000],
                    location_name=location,
                    location_city=city if city in ("Lafayette", "Baton Rouge") else "Other",
                    start_time=start_time,
                    is_free=True,
                    raw_data={"source_library": lib["name"]},
                ))
            except Exception as exc:
                self.log(f"Card parse error: {exc}")

        return events

    def _parse_libcal_datetime(self, date_text: str, time_text: str) -> datetime:
        """Parse 'Feb 24' + 'Tue, 9:00am - 10:00am' into datetime."""
        year = datetime.now().year
        # Parse month/day
        dt = datetime.now()
        try:
            dt = datetime.strptime(f"{date_text} {year}", "%b %d %Y")
        except ValueError:
            pass

        # Parse time from the time_text
        m = re.search(r"(\d{1,2}):(\d{2})(am|pm)", time_text.lower())
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2))
            ampm = m.group(3)
            if ampm == "pm" and hour != 12:
                hour += 12
            if ampm == "am" and hour == 12:
                hour = 0
            dt = dt.replace(hour=hour, minute=minute)

        return dt

    @staticmethod
    def _parse_rss_date(date_str: str) -> datetime:
        """Parse RSS pubDate like 'Mon, 24 Feb 2026 09:00:00 CST'."""
        for fmt in (
            "%a, %d %b %Y %H:%M:%S %Z",
            "%a, %d %b %Y %H:%M:%S %z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        return datetime.now()
