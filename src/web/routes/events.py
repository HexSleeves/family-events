"""Event browsing, detail, and attendance routes."""

from __future__ import annotations

import json
from urllib.parse import quote_plus
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from src.db.models import InterestProfile
from src.ranker.scoring import score_event_breakdown
from src.ranker.weather import WeatherService
from src.web.auth import ensure_csrf_token, get_current_user
from src.web.common import (
    check_rate_limit,
    ctx,
    get_bulk_unattend_undo_store,
    get_db,
    get_templates,
    htmx_redirect_or_redirect,
    hx_target,
    is_htmx_request,
    resolve_event_scope,
    require_login_and_csrf,
    template_response,
    toast,
    visible_city_scope,
)

router = APIRouter()

EVENTS_API_MAX_PER_PAGE = 100


@router.get("/events", response_class=HTMLResponse)
async def events_page(
    request: Request,
    scope: str = "",
    q: str = "",
    city: str = "",
    source: str = "",
    tagged: str = "",
    attended: str = "",
    saved: str = "",
    score_min: str = "",
    sort: str = "start_time",
    page: int = 1,
):
    db = get_db(request)
    user = await get_current_user(request, db)
    resolved_scope = resolve_event_scope(request, user)
    visible_city_slugs = visible_city_scope(user=user, scope=resolved_scope, explicit_city=city)
    if not user:
        attended = ""
        saved = ""
    per_page = 25
    score_min_int = int(score_min) if score_min.isdigit() else None
    events, total = await db.search_events(
        days=30,
        viewer_user_id=user.id if user else None,
        visible_city_slugs=visible_city_slugs,
        q=q,
        city=city,
        source=source,
        tagged=tagged,
        attended=attended,
        saved=saved,
        score_min=score_min_int,
        sort=sort,
        page=page,
        per_page=per_page,
    )
    total_pages = max(1, (total + per_page - 1) // per_page)
    filters = await db.get_filter_options(visible_city_slugs=visible_city_slugs)

    page_ctx = await ctx(
        request,
        active_page="events",
        page_title="Browse Events",
        results_path="/events",
        events=events,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        scope=resolved_scope,
        q=q,
        city=city,
        source=source,
        tagged=tagged,
        attended=attended,
        saved=saved,
        score_min=score_min_int,
        sort=sort,
        cities=filters["cities"],
        sources=filters["sources"],
    )

    if is_htmx_request(request) and hx_target(request) == "events-results":
        return template_response(request, "partials/_events_table.html", page_ctx)
    return template_response(request, "events.html", page_ctx)


@router.get("/event/{event_id}", response_class=HTMLResponse)
async def event_detail(request: Request, event_id: str):
    db = get_db(request)
    user = await get_current_user(request, db)
    event = await db.get_event(event_id, viewer_user_id=user.id if user else None)
    if not event:
        return template_response(
            request,
            "base.html",
            {"request": request, "content": "Event not found."},
            status_code=404,
        )
    raw_data = json.dumps(event.raw_data, indent=2, default=str)[:3000]

    map_query = ", ".join(
        [
            value
            for value in [event.location_name, event.location_address, event.location_city]
            if value
        ]
    )
    maps_url = (
        f"https://www.google.com/maps/search/?api=1&query={quote_plus(map_query)}"
        if map_query
        else None
    )

    related_events: list[tuple[object, float]] = []
    score_breakdown: dict[str, float] | None = None
    if event.tags:
        profile = user.interest_profile if user else InterestProfile()

        start = event.start_time.date()
        weather = await WeatherService().get_weekend_forecast(start, start)
        if event.score_breakdown:
            score_breakdown = event.score_breakdown
        else:
            breakdown = score_event_breakdown(event, profile, weather)
            score_breakdown = {
                "final": breakdown.final,
                "toddler_fit": breakdown.toddler_fit,
                "intrinsic": breakdown.intrinsic,
                "interest": breakdown.interest,
                "weather": breakdown.weather,
                "city": breakdown.city,
                "timing": breakdown.timing,
                "logistics": breakdown.logistics,
                "novelty": breakdown.novelty,
                "confidence": breakdown.confidence,
                "rule_penalty": breakdown.rule_penalty,
                "budget_penalty": breakdown.budget_penalty,
            }

        candidates = await db.get_recent_events(
            days=30,
            viewer_user_id=user.id if user else None,
        )
        related = [
            candidate
            for candidate in candidates
            if candidate.id != event.id
            and candidate.tags
            and candidate.location_city == event.location_city
            and abs((candidate.start_time - event.start_time).days) <= 14
        ]
        related.sort(
            key=lambda candidate: candidate.tags.toddler_score if candidate.tags else 0,
            reverse=True,
        )
        related_events = [
            (candidate, float(candidate.tags.toddler_score if candidate.tags else 0))
            for candidate in related[:4]
        ]

    return template_response(
        request,
        "event_detail.html",
        await ctx(
            request,
            active_page="events",
            event=event,
            raw_data=raw_data,
            maps_url=maps_url,
            related_events=related_events,
            score_breakdown=score_breakdown,
        ),
    )


def _render_event_attendance(request: Request, event, *, target_id: str) -> str:
    return (
        get_templates(request)
        .get_template("partials/_event_attendance.html")
        .render(
            request=request,
            event=event,
            csrf_token=ensure_csrf_token(request),
            target_id=target_id,
        )
    )


@router.post("/api/attend/{event_id}", response_class=HTMLResponse)
async def api_attend(request: Request, event_id: str):
    db = get_db(request)
    user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := check_rate_limit(request, "api_attend"):
        return throttled

    await db.set_event_attended(user.id, event_id, True)
    event = await db.get_event(event_id, viewer_user_id=user.id)
    if event is None:
        raise ValueError("Event disappeared after attend")
    target_id = request.query_params.get("target_id", "event-attendance")
    return toast(
        "Marked attended", body=_render_event_attendance(request, event, target_id=target_id)
    )


@router.post("/api/unattend/{event_id}", response_class=HTMLResponse)
async def api_unattend(request: Request, event_id: str):
    db = get_db(request)
    user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := check_rate_limit(request, "api_unattend"):
        return throttled

    await db.set_event_attended(user.id, event_id, False)
    event = await db.get_event(event_id, viewer_user_id=user.id)
    if event is None:
        raise ValueError("Event disappeared after unattend")
    target_id = request.query_params.get("target_id", "event-attendance")
    return toast(
        "Marked as not attended",
        body=_render_event_attendance(request, event, target_id=target_id),
    )


@router.post("/api/unattend-bulk", response_class=HTMLResponse)
async def api_unattend_bulk(request: Request):
    db = get_db(request)
    user, form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None and form is not None
    if throttled := check_rate_limit(request, "api_unattend_bulk"):
        return throttled

    event_ids = [str(event_id) for event_id in form.getlist("event_ids") if str(event_id).strip()]
    if not event_ids:
        return toast("Select at least one event", "warning", status_code=422)

    await db.set_event_attended_bulk(user.id, event_ids, False)

    undo_token = str(uuid4())
    get_bulk_unattend_undo_store(request)[undo_token] = event_ids
    payload = json.dumps(
        {
            "showToast": {
                "message": f"Updated {len(event_ids)} event(s)",
                "variant": "success",
                "undo": {"path": f"/api/unattend-bulk/undo/{undo_token}", "label": "Undo"},
            }
        }
    )
    return HTMLResponse(content="", status_code=200, headers={"HX-Trigger": payload})


@router.post("/api/unattend-bulk/undo/{undo_token}", response_class=HTMLResponse)
async def api_unattend_bulk_undo(request: Request, undo_token: str):
    db = get_db(request)
    user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := check_rate_limit(request, "api_unattend_bulk_undo"):
        return throttled

    event_ids = get_bulk_unattend_undo_store(request).pop(undo_token, [])
    if not event_ids:
        return toast("Nothing to undo", "warning")

    await db.set_event_attended_bulk(user.id, event_ids, True)
    return toast(f"Restored {len(event_ids)} event(s)")


@router.post("/api/save/{event_id}", response_class=HTMLResponse)
async def api_save(request: Request, event_id: str):
    db = get_db(request)
    user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := check_rate_limit(request, "api_save"):
        return throttled

    await db.set_event_saved(user.id, event_id, True)
    event = await db.get_event(event_id, viewer_user_id=user.id)
    if event is None:
        raise ValueError("Event disappeared after save")
    target_id = request.query_params.get("target_id", "event-attendance")
    return toast("Saved", body=_render_event_attendance(request, event, target_id=target_id))


@router.post("/api/unsave/{event_id}", response_class=HTMLResponse)
async def api_unsave(request: Request, event_id: str):
    db = get_db(request)
    user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := check_rate_limit(request, "api_unsave"):
        return throttled

    await db.set_event_saved(user.id, event_id, False)
    event = await db.get_event(event_id, viewer_user_id=user.id)
    if event is None:
        raise ValueError("Event disappeared after unsave")
    target_id = request.query_params.get("target_id", "event-attendance")
    return toast(
        "Removed from My Events",
        body=_render_event_attendance(request, event, target_id=target_id),
    )


@router.get("/my-events", response_class=HTMLResponse)
async def my_events_page(
    request: Request,
    q: str = "",
    city: str = "",
    source: str = "",
    tagged: str = "",
    attended: str = "",
    saved: str = "",
    sort: str = "-start_time",
    page: int = 1,
):
    db = get_db(request)
    user = await get_current_user(request, db)
    if not user:
        return htmx_redirect_or_redirect(request, "/login")
    per_page = 25
    events, total = await db.list_my_events(
        viewer_user_id=user.id,
        q=q,
        city=city,
        source=source,
        tagged=tagged,
        attended=attended,
        saved=saved,
        sort=sort,
        page=page,
        per_page=per_page,
    )
    total_pages = max(1, (total + per_page - 1) // per_page)
    filters = await db.get_filter_options()
    page_ctx = await ctx(
        request,
        active_page="my-events",
        page_title="My Events",
        results_path="/my-events",
        events=events,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        scope="all",
        q=q,
        city=city,
        source=source,
        tagged=tagged,
        attended=attended,
        saved=saved,
        score_min=None,
        sort=sort,
        cities=filters["cities"],
        sources=filters["sources"],
        is_my_events_page=True,
    )
    if is_htmx_request(request) and hx_target(request) == "events-results":
        return template_response(request, "partials/_events_table.html", page_ctx)
    return template_response(request, "events.html", page_ctx)


@router.get("/api/events")
async def api_events(
    request: Request,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=1, le=EVENTS_API_MAX_PER_PAGE),
    scope: str = "",
    q: str = "",
    city: str = "",
    source: str = "",
    tagged: str = "",
    attended: str = "",
    saved: str = "",
    score_min: int | None = Query(default=None, ge=0, le=10),
    sort: str = "start_time",
):
    user = await get_current_user(request, get_db(request))
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if throttled := check_rate_limit(request, "api_events"):
        return throttled

    if tagged and tagged not in {"yes", "no"}:
        raise HTTPException(status_code=422, detail="tagged must be yes or no")
    if attended and attended not in {"yes", "no"}:
        raise HTTPException(status_code=422, detail="attended must be yes or no")
    if saved and saved not in {"yes", "no"}:
        raise HTTPException(status_code=422, detail="saved must be yes or no")
    if sort not in {
        "start_time",
        "-start_time",
        "title",
        "-title",
        "city",
        "-city",
        "source",
        "-source",
        "score",
        "-score",
    }:
        raise HTTPException(status_code=422, detail="invalid sort")

    db = get_db(request)
    resolved_scope = scope if scope in {"nearby", "all"} else "nearby"
    visible_city_slugs = visible_city_scope(user=user, scope=resolved_scope, explicit_city=city)
    events, total = await db.search_events(
        days=30,
        viewer_user_id=user.id,
        visible_city_slugs=visible_city_slugs,
        q=q,
        city=city,
        source=source,
        tagged=tagged,
        attended=attended,
        saved=saved,
        score_min=score_min,
        sort=sort,
        page=page,
        per_page=per_page,
    )
    return {
        "items": [
            {
                "id": event.id,
                "title": event.title,
                "source": event.source,
                "city": event.location_city,
                "city_slug": event.city_slug,
                "start_time": event.start_time.isoformat(),
                "tagged": event.tags is not None,
                "toddler_score": event.tags.toddler_score if event.tags else None,
                "viewer_state": event.viewer_state.model_dump() if event.viewer_state else None,
            }
            for event in events
        ],
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": max(1, (total + per_page - 1) // per_page),
        },
        "filters": {
            "scope": resolved_scope,
            "q": q,
            "city": city,
            "source": source,
            "tagged": tagged,
            "attended": attended,
            "saved": saved,
            "score_min": score_min,
            "sort": sort,
        },
    }
