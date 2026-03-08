"""Source management routes."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.db.database import Database
from src.db.models import Source
from src.predefined_sources import (
    get_predefined_source,
    list_predefined_sources,
    make_predefined_source,
)
from src.scrapers.analyzer import PageAnalyzer
from src.scrapers.recipe import ScrapeRecipe
from src.scrapers.router import BUILTIN_DOMAIN_MESSAGE, extract_domain, is_builtin_domain
from src.web.auth import ensure_csrf_token, get_current_user
from src.web.common import (
    check_rate_limit,
    ctx,
    get_db,
    get_templates,
    require_login_and_csrf,
    toast,
    validate_source_url,
)
from src.web.jobs_ui import render_job_cards, start_background_job

router = APIRouter()


def _render_source_card(request: Request, source: Source) -> str:
    return get_templates(request).get_template("partials/_source_card.html").render(
        request=request,
        source=source,
        csrf_token=ensure_csrf_token(request),
    )


@router.get("/sources", response_class=HTMLResponse)
async def sources_page(request: Request):
    db = get_db(request)
    user = await get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    sources = await db.get_user_sources(user.id)
    predefined_sources = list_predefined_sources(city=user.home_city)
    recent_jobs = await db.list_jobs(owner_user_id=user.id, limit=10)
    recent_job_cards = render_job_cards(
        recent_jobs,
        target_prefix="job-history-",
        refresh_path="/sources",
        refresh_select="#sources-jobs-panel",
        refresh_target_id="sources-jobs-panel",
    )
    return get_templates(request).TemplateResponse(
        "sources.html",
        await ctx(
            request,
            active_page="sources",
            sources=sources,
            predefined_sources=predefined_sources,
            recent_jobs=recent_jobs,
            recent_job_cards=recent_job_cards,
        ),
    )


@router.get("/source/{source_id}", response_class=HTMLResponse)
async def source_detail(request: Request, source_id: str):
    db = get_db(request)
    user = await get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    source = await db.get_source(source_id)
    if not source:
        return HTMLResponse("Source not found", status_code=404)
    if source.user_id and source.user_id != user.id:
        return HTMLResponse("Forbidden", status_code=403)

    events_from_source, _ = await db.search_events(
        days=90, source=f"custom:{source_id}", per_page=10
    )
    recipe = ScrapeRecipe.model_validate_json(source.recipe_json) if source.recipe_json else None
    recent_jobs = await db.list_jobs(owner_user_id=user.id, source_id=source.id, limit=10)
    recent_job_cards = render_job_cards(
        recent_jobs,
        target_prefix="job-history-",
        refresh_path=f"/source/{source.id}",
        refresh_select="#source-job-history-panel",
        refresh_target_id="source-job-history-panel",
    )
    return get_templates(request).TemplateResponse(
        "source_detail.html",
        await ctx(
            request,
            active_page="sources",
            source=source,
            recipe=recipe,
            events=events_from_source,
            recent_jobs=recent_jobs,
            recent_job_cards=recent_job_cards,
        ),
    )


@router.post("/api/sources", response_class=HTMLResponse)
async def api_add_source(request: Request):
    db = get_db(request)
    user, form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None and form is not None
    if throttled := check_rate_limit(request, "api_add_source"):
        return throttled

    url = str(form.get("url", "")).strip()
    name = str(form.get("name", "")).strip()
    if not url:
        return toast("Please enter a URL", "error")
    if url_error := validate_source_url(url):
        return toast(url_error, "error")
    if is_builtin_domain(url):
        return toast(BUILTIN_DOMAIN_MESSAGE, "info")

    existing = await db.get_user_source_by_url(user.id, url)
    if existing:
        return toast("This URL has already been added", "warning")

    domain = extract_domain(url)
    if not name:
        name = domain.replace(".", " ").title()
    source = Source(
        name=name,
        url=url,
        domain=domain,
        city=user.home_city,
        category="custom",
        status="analyzing",
        user_id=user.id,
    )
    await db.create_source(source)

    db_path = db.db_path

    async def runner(_job) -> dict[str, Any]:
        async with Database(db_path) as job_db:
            try:
                recipe = await PageAnalyzer().analyze(url)
                await job_db.update_source_recipe(
                    source.id,
                    recipe.model_dump_json(),
                    status="active" if recipe.confidence >= 0.3 else "failed",
                )
                return {
                    "summary": f"{recipe.strategy} strategy at {recipe.confidence:.0%} confidence",
                    "strategy": recipe.strategy,
                    "confidence": recipe.confidence,
                    "notes": recipe.notes,
                    "recipe": recipe.model_dump(mode="json"),
                }
            except Exception as exc:
                await job_db.update_source_status(source.id, status="failed", error=str(exc))
                raise

    return await start_background_job(
        request,
        user=user,
        kind="source-analyze",
        key=f"source:analyze:{source.id}",
        label=f"Analyzing {source.name}",
        runner=runner,
        target_id="sources-job-status",
        source_id=source.id,
    )


@router.post("/api/sources/{source_id}/analyze", response_class=HTMLResponse)
async def api_reanalyze(request: Request, source_id: str):
    db = get_db(request)
    user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := check_rate_limit(request, "api_reanalyze_source"):
        return throttled

    source = await db.get_source(source_id)
    if not source:
        return HTMLResponse("Not found", status_code=404)
    if source.user_id and source.user_id != user.id:
        return HTMLResponse("Forbidden", status_code=403)
    await db.update_source_status(source_id, status="analyzing")

    db_path = db.db_path

    async def runner(_job) -> dict[str, Any]:
        async with Database(db_path) as job_db:
            source_for_job = await job_db.get_source(source_id)
            if not source_for_job:
                raise ValueError("Source not found")
            try:
                recipe = await PageAnalyzer().analyze(source_for_job.url)
                await job_db.update_source_recipe(
                    source_id,
                    recipe.model_dump_json(),
                    status="active" if recipe.confidence >= 0.3 else "failed",
                )
                return {
                    "summary": f"{recipe.strategy} strategy at {recipe.confidence:.0%} confidence",
                    "strategy": recipe.strategy,
                    "confidence": recipe.confidence,
                    "notes": recipe.notes,
                    "recipe": recipe.model_dump(mode="json"),
                }
            except Exception as exc:
                await job_db.update_source_status(source_id, status="failed", error=str(exc))
                raise

    return await start_background_job(
        request,
        user=user,
        kind="source-analyze",
        key=f"source:analyze:{source_id}",
        label=f"Analyzing {source.name}",
        runner=runner,
        target_id=f"source-job-{source_id}",
        source_id=source_id,
    )


@router.post("/api/sources/{source_id}/test", response_class=HTMLResponse)
async def api_test_source(request: Request, source_id: str):
    db = get_db(request)
    user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := check_rate_limit(request, "api_test_source"):
        return throttled

    source = await db.get_source(source_id)
    if not source or not source.recipe_json:
        return HTMLResponse("No recipe to test", status_code=400)
    if source.user_id and source.user_id != user.id:
        return HTMLResponse("Forbidden", status_code=403)

    db_path = db.db_path

    async def runner(_job) -> dict[str, Any]:
        from src.scrapers.generic import GenericScraper

        async with Database(db_path) as job_db:
            source_for_job = await job_db.get_source(source_id)
            if not source_for_job or not source_for_job.recipe_json:
                raise ValueError("No recipe to test")
            recipe = ScrapeRecipe.model_validate_json(source_for_job.recipe_json)
            scraper = GenericScraper(
                url=source_for_job.url,
                source_id=source_for_job.id,
                recipe=recipe,
            )
            events = await scraper.scrape()
            sample_events = [
                {
                    "title": event.title,
                    "start_time": event.start_time.isoformat(),
                    "location_name": event.location_name,
                    "location_city": event.location_city,
                    "source_url": event.source_url,
                }
                for event in events[:5]
            ]
            return {
                "summary": f"{len(events)} events found",
                "count": len(events),
                "sample_events": sample_events,
                "source_url": source_for_job.url,
                "strategy": recipe.strategy,
            }

    return await start_background_job(
        request,
        user=user,
        kind="source-test",
        key=f"source:test:{source_id}",
        label=f"Testing {source.name}",
        runner=runner,
        target_id=f"source-job-{source_id}",
        source_id=source_id,
    )


@router.post("/api/sources/{source_id}/toggle", response_class=HTMLResponse)
async def api_toggle_source(request: Request, source_id: str):
    db = get_db(request)
    user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := check_rate_limit(request, "api_toggle_source"):
        return throttled

    source = await db.get_source(source_id)
    if not source:
        return HTMLResponse("Not found", status_code=404)
    if source.user_id and source.user_id != user.id:
        return HTMLResponse("Forbidden", status_code=403)

    enabled = await db.toggle_source(source_id)
    updated = await db.get_source(source_id)
    if updated is None:
        raise ValueError("Source disappeared after toggle")
    state = "enabled" if enabled else "disabled"
    return HTMLResponse(
        content=_render_source_card(request, updated),
        headers={
            "HX-Trigger": json.dumps(
                {
                    "showToast": {"message": f"Source {state}", "variant": "success"},
                }
            )
        },
    )


@router.delete("/api/sources/{source_id}", response_class=HTMLResponse)
async def api_delete_source(request: Request, source_id: str):
    db = get_db(request)
    user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := check_rate_limit(request, "api_delete_source"):
        return throttled

    source = await db.get_source(source_id)
    if not source:
        return HTMLResponse("Not found", status_code=404)
    if source.user_id and source.user_id != user.id:
        return HTMLResponse("Forbidden", status_code=403)

    await db.delete_source(source_id)
    return HTMLResponse(
        content="",
        headers={
            "HX-Trigger": json.dumps(
                {
                    "showToast": {"message": "Source deleted", "variant": "success"},
                }
            )
        },
    )


@router.post("/api/sources/predefined", response_class=HTMLResponse)
async def api_add_predefined_source(request: Request):
    db = get_db(request)
    user, form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None and form is not None
    source_key = str(form.get("source_key", "")).strip()
    if not source_key:
        return toast("Choose a source to add", "error")
    try:
        source_config = get_predefined_source(source_key)
    except KeyError:
        return toast("Unknown source", "error", status_code=404)
    existing = await db.get_user_source_by_url(user.id, source_config["url"])
    if existing:
        return toast("Source already added", "warning")
    await db.create_source(make_predefined_source(user_id=user.id, source_key=source_key))
    return toast(
        f"Added {source_config['name']}",
        body='<script>setTimeout(()=>location.reload(),300)</script>',
    )
