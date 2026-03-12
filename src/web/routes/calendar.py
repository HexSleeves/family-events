"""Calendar page and ICS export routes."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from io import StringIO
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from src.timezones import as_local_date, local_date_range_utc, local_today, utc_now
from src.web.auth import get_current_user
from src.web.common import (
    ctx,
    get_db,
    is_htmx_request,
    resolve_event_scope,
    template_response,
    visible_city_scope,
)

router = APIRouter()


def _resolve_month_range(
    month: str,
) -> tuple[date, date, date, date]:
    today = local_today()
    if month:
        try:
            month_date = datetime.strptime(month, "%Y-%m").date()
            month_start = month_date.replace(day=1)
        except ValueError:
            month_start = today.replace(day=1)
    else:
        month_start = today.replace(day=1)

    if month_start.month == 12:
        next_month_start = month_start.replace(year=month_start.year + 1, month=1, day=1)
    else:
        next_month_start = month_start.replace(month=month_start.month + 1, day=1)

    prev_month = (
        month_start.replace(year=month_start.year - 1, month=12, day=1)
        if month_start.month == 1
        else month_start.replace(month=month_start.month - 1, day=1)
    )
    return today, month_start, next_month_start, prev_month


@router.get("/calendar", response_class=HTMLResponse)
@router.get("/calendars", response_class=HTMLResponse)
async def calendar_page(request: Request, month: str = "", attended: str = "", scope: str = ""):
    db = get_db(request)
    user = await get_current_user(request, db)
    resolved_scope = resolve_event_scope(request, user)
    visible_city_slugs = visible_city_scope(user=user, scope=resolved_scope)
    if not user:
        attended = ""
    today, month_start, next_month_start, prev_month = _resolve_month_range(month)

    range_start, range_end = local_date_range_utc(month_start, next_month_start)
    events = await db.get_events_between(
        range_start,
        range_end,
        viewer_user_id=user.id if user else None,
        visible_city_slugs=visible_city_slugs,
        attended=attended,
    )
    events.sort(
        key=lambda event: (
            event.start_time.astimezone(UTC)
            if event.start_time.tzinfo is not None
            else event.start_time.replace(tzinfo=UTC)
        )
    )

    events_by_day: dict[str, list[Any]] = {}
    for event in events:
        key = as_local_date(event.start_time).isoformat()
        events_by_day.setdefault(key, []).append(event)

    first_weekday = month_start.weekday()
    grid_start = month_start - timedelta(days=first_weekday)
    days: list[dict[str, Any]] = []
    for offset in range(42):
        day = grid_start + timedelta(days=offset)
        key = day.isoformat()
        day_events = events_by_day.get(key, [])
        days.append(
            {
                "date": day,
                "key": key,
                "in_month": day.month == month_start.month,
                "is_today": day == today,
                "is_weekend": day.weekday() >= 5,
                "events": day_events,
                "event_count": len(day_events),
            }
        )

    weeks = [days[i : i + 7] for i in range(0, len(days), 7)]
    month_days = [day for day in days if day["in_month"]]
    active_days = [day for day in month_days if day["event_count"]]
    attended_events = [event for event in events if getattr(event, "attended", False)]
    free_events = [event for event in events if getattr(event, "is_free", False)]
    cities = sorted({event.location_city for event in events if event.location_city})
    sources = sorted({event.source for event in events if event.source})
    featured_days = sorted(active_days, key=lambda day: day["event_count"], reverse=True)[:3]
    upcoming_events = [event for event in events if as_local_date(event.start_time) >= today][:8]

    page_ctx = await ctx(
        request,
        active_page="calendar",
        month_start=month_start,
        month_label=month_start.strftime("%B %Y"),
        prev_month=prev_month,
        next_month=next_month_start,
        scope=resolved_scope,
        attended=attended,
        total_events=len(events),
        attended_events_count=len(attended_events),
        free_events_count=len(free_events),
        busy_days_count=len(active_days),
        source_count=len(sources),
        city_count=len(cities),
        weeks=weeks,
        featured_days=featured_days,
        upcoming_events=upcoming_events,
        cities=cities,
        sources=sources,
        today=today,
    )

    if is_htmx_request(request):
        return template_response(request, "partials/_calendar_shell.html", page_ctx)
    return template_response(request, "calendar.html", page_ctx)


@router.get("/calendar.ics")
async def calendar_ics(request: Request, month: str = "", attended: str = "", scope: str = ""):
    db = get_db(request)
    user = await get_current_user(request, db)
    resolved_scope = resolve_event_scope(request, user)
    visible_city_slugs = visible_city_scope(user=user, scope=resolved_scope)
    if not user:
        attended = ""
    _today, month_start, next_month_start, _prev_month = _resolve_month_range(month)

    range_start, range_end = local_date_range_utc(month_start, next_month_start)
    events = await db.get_events_between(
        range_start,
        range_end,
        viewer_user_id=user.id if user else None,
        visible_city_slugs=visible_city_slugs,
        attended=attended,
    )

    def esc(value: str) -> str:
        return (
            value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
        )

    out = StringIO()
    out.write("BEGIN:VCALENDAR\r\n")
    out.write("VERSION:2.0\r\n")
    out.write("PRODID:-//Family Events//Calendar Export//EN\r\n")
    out.write("CALSCALE:GREGORIAN\r\n")
    generated = utc_now().strftime("%Y%m%dT%H%M%SZ")

    for event in events:
        start = event.start_time.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        end_dt = event.end_time or (event.start_time + timedelta(hours=2))
        end = end_dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        out.write("BEGIN:VEVENT\r\n")
        out.write(f"UID:{event.id}@family-events\r\n")
        out.write(f"DTSTAMP:{generated}\r\n")
        out.write(f"DTSTART:{start}\r\n")
        out.write(f"DTEND:{end}\r\n")
        out.write(f"SUMMARY:{esc(event.title)}\r\n")
        location = ", ".join(
            [
                value
                for value in [event.location_name, event.location_address, event.location_city]
                if value
            ]
        )
        if location:
            out.write(f"LOCATION:{esc(location)}\r\n")
        if event.description:
            out.write(f"DESCRIPTION:{esc(event.description)}\r\n")
        out.write(f"URL:{esc(event.source_url)}\r\n")
        out.write("END:VEVENT\r\n")

    out.write("END:VCALENDAR\r\n")
    filename = f"family-events-{month_start.strftime('%Y-%m')}.ics"
    return Response(
        content=out.getvalue(),
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
