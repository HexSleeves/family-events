"""Lafayette, Louisiana event scrapers.

Aggregates from multiple Lafayette-area sources:
- Moncus Park (moncuspark.org) — MEC WordPress calendar
- Acadiana Center for the Arts — MEC WordPress calendar
- Lafayette Science Museum — MEC WordPress calendar

All three use Modern Events Calendar (MEC) plugin, so parsing logic is shared.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
from datetime import datetime

from bs4 import BeautifulSoup, Tag

from src.db.models import Event

from .base import BaseScraper

MEC_SOURCES = [
    {
        "name": "Moncus Park",
        "url": "https://moncuspark.org/events/",
        "base": "https://moncuspark.org",
        "sub": "moncus_park",
    },
    {
        "name": "Acadiana Center for the Arts",
        "url": "https://acadianacenterforthearts.org/events/",
        "base": "https://acadianacenterforthearts.org",
        "sub": "acadiana_arts",
    },
    {
        "name": "Lafayette Science Museum",
        "url": "https://lafayettesciencemuseum.org/events",
        "base": "https://lafayettesciencemuseum.org",
        "sub": "science_museum",
    },
]


class LafayetteScraper(BaseScraper):
    source_name = "lafayette"

    async def scrape(self) -> list[Event]:
        all_events: list[Event] = []
        for src in MEC_SOURCES:
            try:
                events = await self._scrape_mec(src)
                self.log(f"{src['name']}: {len(events)} events")
                all_events.extend(events)
            except Exception as exc:
                self.log(f"{src['name']} failed: {exc}")
        self.log(f"Total Lafayette: {len(all_events)}")
        return all_events

    async def _scrape_mec(self, src: dict) -> list[Event]:
        async with self._client() as client:
            resp = await client.get(src["url"])
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        events: list[Event] = []

        # Try MEC article cards first
        articles = soup.select(".mec-event-article, .type-mec-events")
        if articles:
            for art in articles:
                ev = self._parse_mec_article(art, src)
                if ev:
                    events.append(ev)

        # Also extract from event links (catches calendar-view events)
        events.extend(self._extract_event_links(soup, src))

        # Deduplicate by source_id
        seen: set[str] = set()
        unique: list[Event] = []
        for ev in events:
            if ev.source_id not in seen:
                seen.add(ev.source_id)
                unique.append(ev)
        return unique

    def _parse_mec_article(self, art: Tag, src: dict) -> Event | None:
        title_el = art.select_one(".mec-event-title a, h4 a, h3 a, h2 a")
        if not title_el:
            return None

        title = title_el.get_text(strip=True)
        href = str(title_el.get("href") or "")
        if href and not href.startswith("http"):
            href = f"{src['base']}{href}"

        date_text = ""
        for sel in (".mec-event-date", ".mec-start-date-label", ".mec-date"):
            el = art.select_one(sel)
            if el:
                date_text = el.get_text(strip=True)
                break

        time_text = ""
        for sel in (".mec-event-time", ".mec-time"):
            el = art.select_one(sel)
            if el:
                time_text = el.get_text(strip=True)
                break

        desc = ""
        desc_el = art.select_one(".mec-event-description, p")
        if desc_el:
            desc = desc_el.get_text(strip=True)[:500]

        img_el = art.select_one("img")
        image = str(img_el.get("src", "")) if img_el else None

        start_time = _parse_mec_dt(date_text, time_text)

        m = re.search(r"/events?/([^/?]+)", href)
        sid = (
            f"{src['sub']}_{m.group(1)}"
            if m
            else hashlib.md5(f"{src['sub']}_{title}_{date_text}".encode()).hexdigest()
        )

        return Event(
            source=self.source_name,
            source_url=href or src["url"],
            source_id=sid,
            title=title,
            description=desc,
            location_name=src["name"],
            location_city="Lafayette",
            start_time=start_time,
            image_url=image,
            raw_data={
                "sub": src["sub"],
                "venue": src["name"],
                "date_raw": date_text,
                "time_raw": time_text,
            },
        )

    def _extract_event_links(self, soup: BeautifulSoup, src: dict) -> list[Event]:
        """Fallback: gather events from <a> links to event detail pages."""
        events: list[Event] = []
        seen: set[str] = set()
        skip = {
            "events",
            "all events",
            "view all",
            "private events",
            "special events",
            "member events",
            "show more dates >>",
        }

        for link in soup.select('a[href*="events/"]'):
            href = str(link.get("href") or "")
            text = link.get_text(strip=True)
            if not text or len(text) < 3 or text.lower() in skip:
                continue
            if href in seen:
                continue
            seen.add(href)
            if not href.startswith("http"):
                href = f"{src['base']}{href}"

            start_time = datetime.now()
            occ = re.search(r"occurrence=(\d{4}-\d{2}-\d{2})", href)
            if occ:
                with contextlib.suppress(ValueError):
                    start_time = datetime.strptime(occ.group(1), "%Y-%m-%d")

            m = re.search(r"/events?/([^/?]+)", href)
            sid = f"{src['sub']}_{m.group(1)}" if m else hashlib.md5(href.encode()).hexdigest()

            events.append(
                Event(
                    source=self.source_name,
                    source_url=href,
                    source_id=sid,
                    title=text,
                    location_name=src["name"],
                    location_city="Lafayette",
                    start_time=start_time,
                    raw_data={"sub": src["sub"], "venue": src["name"]},
                )
            )
        return events


def _apply_time(dt: datetime, time_text: str) -> datetime:
    if not time_text:
        return dt
    m = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)", time_text.lower())
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3)
        if ap == "pm" and h != 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        return dt.replace(hour=h, minute=mi)
    return dt


def _parse_mec_dt(date_text: str, time_text: str) -> datetime:
    """Parse MEC date formats like '28February2026', 'Saturday - 07 Mar', etc."""
    now = datetime.now()
    dt = now

    # '28February2026' (no spaces)
    m = re.match(r"(\d{1,2})([A-Za-z]+)(\d{4})", date_text.strip())
    if m:
        try:
            dt = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %B %Y")
            return _apply_time(dt, time_text)
        except ValueError:
            pass

    # 'Saturday - 07 Mar'
    m = re.match(r"\w+\s*-\s*(\d{1,2})\s+([A-Za-z]+)", date_text.strip())
    if m:
        try:
            dt = datetime.strptime(f"{m.group(1)} {m.group(2)} {now.year}", "%d %b %Y")
            if dt < now:
                dt = dt.replace(year=now.year + 1)
            return _apply_time(dt, time_text)
        except ValueError:
            pass

    # Embedded date anywhere
    m = re.search(r"(\d{1,2})([A-Za-z]+)(\d{4})", date_text)
    if m:
        try:
            dt = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %B %Y")
            return _apply_time(dt, time_text)
        except ValueError:
            pass

    for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return _apply_time(datetime.strptime(date_text.strip(), fmt), time_text)
        except ValueError:
            continue

    return _apply_time(dt, time_text)
