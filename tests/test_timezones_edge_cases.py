from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from src.db.models import Source
from src.scrapers.allevents import _parse_dt as parse_allevents_dt
from src.scrapers.brec import BrecScraper
from src.scrapers.eventbrite import EventbriteScraper
from src.scrapers.generic import GenericScraper
from src.scrapers.lafayette import _parse_mec_dt
from src.scrapers.library import LibraryScraper
from src.timezones import APP_TZ, as_local_date, local_date_range_utc, local_today


def test_local_today_uses_app_timezone_around_utc_midnight() -> None:
    assert local_today(now=datetime(2025, 3, 8, 5, 30, tzinfo=UTC)) == date(2025, 3, 7)
    assert local_today(now=datetime(2025, 3, 8, 6, 30, tzinfo=UTC)) == date(2025, 3, 8)


def test_local_date_range_utc_handles_dst_length_changes() -> None:
    spring_start, spring_end = local_date_range_utc(date(2025, 3, 9), date(2025, 3, 10))
    fall_start, fall_end = local_date_range_utc(date(2025, 11, 2), date(2025, 11, 3))

    assert spring_start == datetime(2025, 3, 9, 6, 0, tzinfo=UTC)
    assert spring_end == datetime(2025, 3, 10, 5, 0, tzinfo=UTC)
    assert spring_end - spring_start == timedelta(hours=23)

    assert fall_start == datetime(2025, 11, 2, 5, 0, tzinfo=UTC)
    assert fall_end == datetime(2025, 11, 3, 6, 0, tzinfo=UTC)
    assert fall_end - fall_start == timedelta(hours=25)


def test_generic_scraper_assumes_app_timezone_for_naive_times() -> None:
    parsed = GenericScraper._parse_dt("2025-03-09 00:30")

    assert parsed == datetime(2025, 3, 9, 0, 30, tzinfo=APP_TZ)
    assert as_local_date(parsed) == date(2025, 3, 9)


def test_eventbrite_parse_dt_preserves_utc_suffix() -> None:
    parsed = EventbriteScraper._parse_dt("2025-03-09T06:30:00Z")

    assert parsed == datetime(2025, 3, 9, 6, 30, tzinfo=UTC)
    assert as_local_date(parsed) == date(2025, 3, 9)


def test_allevents_parse_dt_assumes_app_timezone_for_date_only_strings() -> None:
    parsed = parse_allevents_dt("2025-03-09")

    assert parsed == datetime(2025, 3, 9, 0, 0, tzinfo=APP_TZ)
    assert as_local_date(parsed) == date(2025, 3, 9)


def test_library_rss_dates_stay_timezone_aware() -> None:
    parsed = LibraryScraper._parse_rss_date("Sun, 09 Mar 2025 06:30:00 GMT")

    assert parsed == datetime(2025, 3, 9, 6, 30, tzinfo=UTC)
    assert as_local_date(parsed) == date(2025, 3, 9)


def test_lafayette_mec_parser_anchors_midnight_times_to_app_timezone() -> None:
    parsed = _parse_mec_dt("March 09, 2025", "12:30 am")

    assert parsed == datetime(2025, 3, 9, 0, 30, tzinfo=APP_TZ)
    assert as_local_date(parsed) == date(2025, 3, 9)


def test_brec_parser_anchors_midnight_times_to_app_timezone() -> None:
    scraper = BrecScraper(
        Source(
            name="BREC",
            url="https://www.brec.org/calendar",
            domain="brec.org",
            city="Baton Rouge",
        )
    )

    parsed = scraper._parse_date_time("Sunday, March 9, 2025", "12:30 AM - 1:30 AM")

    assert parsed == datetime(2025, 3, 9, 0, 30, tzinfo=APP_TZ)
    assert as_local_date(parsed) == date(2025, 3, 9)
