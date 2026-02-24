"""BREC (Baton Rouge Recreation and Park Commission) scraper.

Scrapes: https://www.brec.org/calendar
The page renders server-side HTML with <article> blocks inside a .events-list section.
Each article has h3 (title), .time, .park, and an <a> link to details.
Day headers are <header class="day-header"> with <h2> date text.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta

from bs4 import BeautifulSoup, Tag

from src.db.models import Event
from .base import BaseScraper

BASE_URL = "https://www.brec.org"
CALENDAR_URL = f"{BASE_URL}/calendar"


class BrecScraper(BaseScraper):
    source_name = "brec"

    async def scrape(self, enrich: bool = False) -> list[Event]:
        """Scrape current month + next month from BREC calendar.
        
        Args:
            enrich: If True, fetch detail pages for descriptions (slower but better for LLM tagging).
        """
        all_events: list[Event] = []
        try:
            events = await self._scrape_month(CALENDAR_URL)
            all_events.extend(events)
        except Exception as exc:
            self.log(f"Current month failed: {exc}")

        # Also scrape next month
        try:
            now = datetime.now()
            next_month = (now.replace(day=1) + timedelta(days=32)).replace(day=1)
            next_url = f"{CALENDAR_URL}/{next_month.strftime('%Y/%m')}"
            events = await self._scrape_month(next_url)
            all_events.extend(events)
        except Exception as exc:
            self.log(f"Next month failed: {exc}")

        # Optionally enrich events with descriptions from detail pages
        if enrich:
            all_events = await self._enrich_events(all_events)

        self.log(f"Total: {len(all_events)} events")
        return all_events

    async def _enrich_events(self, events: list[Event], max_concurrent: int = 5) -> list[Event]:
        """Fetch detail pages to get full descriptions."""
        import asyncio
        sem = asyncio.Semaphore(max_concurrent)
        
        async def enrich_one(event: Event) -> Event:
            if event.description or not event.source_url or event.source_url == CALENDAR_URL:
                return event
            async with sem:
                try:
                    async with self._client() as client:
                        resp = await client.get(event.source_url)
                        if resp.status_code == 200:
                            soup = BeautifulSoup(resp.text, "html.parser")
                            desc_el = soup.select_one(".event-description, .event-detail, .description, article p, main p")
                            if desc_el:
                                event.description = desc_el.get_text(separator=" ", strip=True)[:2000]
                except Exception:
                    pass
            return event
        
        # Only enrich a sample to avoid hammering the server
        to_enrich = [e for e in events if not e.description][:50]
        self.log(f"Enriching {len(to_enrich)} events with descriptions...")
        enriched = await asyncio.gather(*[enrich_one(e) for e in to_enrich])
        
        # Merge back
        enriched_map = {e.source_id: e for e in enriched}
        return [enriched_map.get(e.source_id, e) for e in events]

    async def _scrape_month(self, url: str) -> list[Event]:
        async with self._client() as client:
            resp = await client.get(url)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        events_list = soup.select_one(".events-list")
        if not events_list:
            self.log(f"No .events-list found at {url}")
            return []

        events: list[Event] = []
        current_date_str = ""

        for el in events_list.children:
            if not isinstance(el, Tag):
                continue

            # Day headers: <header class="day-header"><h2>Sunday, February 1, 2026</h2></header>
            if el.name == "header" and "day-header" in el.get("class", []):
                h2 = el.select_one("h2")
                if h2:
                    current_date_str = h2.get_text(strip=True)
                continue

            # Event articles: <article>...<h3>Title</h3>...<span class="time">...</span>...<span class="park">...</span>...<a href="/calendar/detail/...">...
            if el.name == "article":
                try:
                    event = self._parse_article(el, current_date_str)
                    if event:
                        events.append(event)
                except Exception as exc:
                    self.log(f"Parse error: {exc}")

        self.log(f"{url}: {len(events)} events")
        return events

    def _parse_article(self, article: Tag, date_str: str) -> Event | None:
        # Title
        h3 = article.select_one("h3")
        title = h3.get_text(strip=True) if h3 else ""
        if not title:
            return None

        # Link
        link_el = article.select_one("a[href]")
        href = link_el["href"] if link_el else ""
        if href and not href.startswith("http"):
            href = f"{BASE_URL}{href}"

        # Time
        time_el = article.select_one(".time")
        time_text = time_el.get_text(" ", strip=True) if time_el else "all day"

        # Park/location
        park_el = article.select_one(".park")
        park = park_el.get_text(strip=True) if park_el else ""

        # Image
        img_el = article.select_one("img")
        image = None
        if img_el:
            image = img_el.get("src", "")
            if image and not image.startswith("http"):
                image = f"{BASE_URL}{image}"

        # Parse date + time into datetime
        start_time = self._parse_date_time(date_str, time_text)
        end_time = self._parse_end_time(date_str, time_text)

        # Source ID from URL or hash
        source_id = ""
        if href:
            # Extract slug from /calendar/detail/slug/12345
            m = re.search(r"/calendar/detail/[^/]+/(\d+)", href)
            source_id = m.group(1) if m else hashlib.md5(href.encode()).hexdigest()
        else:
            source_id = hashlib.md5(f"{title}{date_str}".encode()).hexdigest()

        return Event(
            source=self.source_name,
            source_url=href or CALENDAR_URL,
            source_id=source_id,
            title=title,
            description="",  # description is on the detail page
            location_name=park,
            location_city="Baton Rouge",
            start_time=start_time,
            end_time=end_time,
            image_url=image,
            raw_data={"date_header": date_str, "time_text": time_text, "park": park},
        )

    def _parse_date_time(self, date_str: str, time_text: str) -> datetime:
        """Parse 'Sunday, February 1, 2026' + '8:30 AM - 9:30 AM' into a datetime."""
        # Parse the date part
        dt = self._parse_date_header(date_str)

        # Parse start time from time_text
        time_text = time_text.strip().lower()
        if "all day" in time_text or not time_text:
            return dt.replace(hour=0, minute=0)

        # Try to extract start time like "8:30 AM" or "8:30 am - 9:30 am"
        m = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)", time_text)
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2))
            ampm = m.group(3)
            if ampm == "pm" and hour != 12:
                hour += 12
            if ampm == "am" and hour == 12:
                hour = 0
            return dt.replace(hour=hour, minute=minute)

        return dt

    def _parse_end_time(self, date_str: str, time_text: str) -> datetime | None:
        """Parse end time from time text like '8:30 AM - 9:30 AM'."""
        time_text = time_text.strip().lower()
        if "all day" in time_text or "-" not in time_text:
            return None

        # Get the part after the dash
        parts = time_text.split("-")
        if len(parts) < 2:
            return None

        end_part = parts[-1].strip()
        m = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)", end_part)
        if not m:
            return None

        dt = self._parse_date_header(date_str)
        hour = int(m.group(1))
        minute = int(m.group(2))
        ampm = m.group(3)
        if ampm == "pm" and hour != 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        return dt.replace(hour=hour, minute=minute)

    @staticmethod
    def _parse_date_header(date_str: str) -> datetime:
        """Parse 'Sunday, February 1, 2026' into datetime."""
        date_str = date_str.strip()
        for fmt in (
            "%A, %B %d, %Y",
            "%B %d, %Y",
            "%m/%d/%Y",
        ):
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        raise ValueError(f"Cannot parse date header: {date_str!r}")
