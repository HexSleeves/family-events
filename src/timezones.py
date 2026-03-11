"""Shared application timezone helpers."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

APP_TZ = ZoneInfo("America/Chicago")


def utc_now() -> datetime:
    """Return the current UTC time as an aware datetime."""
    return datetime.now(tz=UTC)


def local_now(*, now: datetime | None = None) -> datetime:
    """Return the current application-local time."""
    current = now or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(APP_TZ)


def local_today(*, now: datetime | None = None) -> date:
    """Return today's date in the application timezone."""
    return local_now(now=now).date()


def local_date_start_utc(value: date) -> datetime:
    """Return the UTC instant for local midnight at the given date."""
    return datetime.combine(value, time.min, tzinfo=APP_TZ).astimezone(UTC)


def local_date_end_exclusive_utc(value: date) -> datetime:
    """Return the UTC instant for the next local midnight after the date."""
    return local_date_start_utc(value + timedelta(days=1))


def local_date_range_utc(start: date, end_exclusive: date) -> tuple[datetime, datetime]:
    """Convert a local date range [start, end_exclusive) to UTC instants."""
    return local_date_start_utc(start), local_date_start_utc(end_exclusive)


def current_weekend_dates(
    *, now: datetime | None = None, roll_after_saturday_noon: bool = False
) -> tuple[date, date]:
    """Return the target Saturday/Sunday in the application timezone."""
    local = local_now(now=now)
    today = local.date()
    days_until_sat = (5 - today.weekday()) % 7
    if roll_after_saturday_noon and days_until_sat == 0 and local.hour >= 12:
        days_until_sat = 7
    saturday = today + timedelta(days=days_until_sat)
    return saturday, saturday + timedelta(days=1)


def weekend_window_utc(saturday: date, sunday: date) -> tuple[datetime, datetime]:
    """Return the UTC window covering the local Saturday/Sunday weekend."""
    return local_date_range_utc(saturday, sunday + timedelta(days=1))


def as_local_date(value: datetime) -> date:
    """Convert an aware datetime to an application-local date."""
    current = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return current.astimezone(APP_TZ).date()
