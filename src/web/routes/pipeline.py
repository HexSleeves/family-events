"""Pipeline action routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.db.database import create_database
from src.tagger.taxonomy import TAGGING_VERSION
from src.web.common import check_rate_limit, get_db, require_login_and_csrf
from src.web.jobs_ui import start_background_job

router = APIRouter()


@router.post("/api/scrape-tag", response_class=HTMLResponse)
async def api_scrape_tag(request: Request):
    db = get_db(request)
    user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := check_rate_limit(request, "api_scrape_tag"):
        return throttled

    from src.scheduler import run_scrape_then_tag

    database_url = db.database_url

    async def runner(job) -> dict[str, int | str]:
        async with create_database(database_url=database_url) as job_db:
            await job.update(
                detail="Preparing scrape + tag run…",
                result={
                    "phase": "scrape",
                    "processed": 0,
                    "total": 2,
                    "summary": "Scraping sources…",
                },
            )
            return await run_scrape_then_tag(
                job_db,
                include_stale=False,
                progress_callback=lambda progress: job.update(
                    detail=progress.get("summary", "Running…"), result=progress
                ),
            )

    return await start_background_job(
        request,
        user=user,
        database_url=database_url,
        kind="pipeline",
        key="pipeline:scrape-tag",
        label="Scrape + tag job",
        runner=runner,
        target_id="dashboard-job-status",
    )


@router.post("/api/scrape", response_class=HTMLResponse)
async def api_scrape(request: Request):
    db = get_db(request)
    user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := check_rate_limit(request, "api_scrape"):
        return throttled

    from src.scheduler import run_scrape

    database_url = db.database_url

    async def runner(_job) -> int:
        async with create_database(database_url=database_url) as job_db:
            return await run_scrape(job_db)

    return await start_background_job(
        request,
        user=user,
        database_url=database_url,
        kind="scrape",
        key="pipeline:scrape",
        label="Scrape job",
        runner=runner,
        target_id="dashboard-job-status",
    )


@router.post("/api/tag", response_class=HTMLResponse)
async def api_tag(request: Request):
    db = get_db(request)
    user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := check_rate_limit(request, "api_tag"):
        return throttled

    from src.scheduler import run_tag

    database_url = db.database_url

    async def runner(job) -> int:
        async with create_database(database_url=database_url) as job_db:
            await job.update(
                detail="Preparing tag batches…",
                result={"processed": 0, "total": 0, "succeeded": 0, "failed": 0},
            )
            return await run_tag(
                job_db,
                include_stale=False,
                progress_callback=lambda progress: job.update(
                    detail=progress.get("summary", "Running…"), result=progress
                ),
            )

    return await start_background_job(
        request,
        user=user,
        database_url=database_url,
        kind="tag",
        key="pipeline:tag",
        label="Tag job",
        runner=runner,
        target_id="dashboard-job-status",
    )


@router.post("/api/dedupe", response_class=HTMLResponse)
async def api_dedupe(request: Request):
    db = get_db(request)
    user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := check_rate_limit(request, "api_dedupe"):
        return throttled

    database_url = db.database_url

    async def runner(_job) -> int:
        async with create_database(database_url=database_url) as job_db:
            result = await job_db.dedupe_existing_events()
            return int(result["merged"])

    return await start_background_job(
        request,
        user=user,
        database_url=database_url,
        kind="dedupe",
        key="pipeline:dedupe",
        label="Dedupe job",
        runner=runner,
        target_id="dashboard-job-status",
    )


@router.post("/api/notify", response_class=HTMLResponse)
async def api_notify(request: Request):
    db = get_db(request)
    user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := check_rate_limit(request, "api_notify"):
        return throttled

    from src.scheduler import run_notify

    database_url = db.database_url

    async def runner(_job) -> dict[str, object]:
        async with create_database(database_url=database_url) as job_db:
            return await run_notify(job_db, user=user)

    return await start_background_job(
        request,
        user=user,
        database_url=database_url,
        kind="notify",
        key=f"pipeline:notify:{user.id}",
        label="Notification job",
        runner=runner,
        target_id="dashboard-job-status",
    )


@router.post("/api/tag/stale", response_class=HTMLResponse)
async def api_tag_stale(request: Request):
    db = get_db(request)
    user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None
    if throttled := check_rate_limit(request, "api_tag_stale"):
        return throttled

    from src.scheduler import run_tag

    database_url = db.database_url

    async def runner(job) -> int:
        async with create_database(database_url=database_url) as job_db:
            stale_count = await job_db.count_stale_tagged_events(tagging_version=TAGGING_VERSION)
            await job.update(
                detail="Preparing stale retag batches…",
                result={
                    "processed": 0,
                    "total": stale_count,
                    "succeeded": 0,
                    "failed": 0,
                    "summary": f"0/{stale_count} processed · 0 tagged · 0 failed",
                },
            )
            return await run_tag(
                job_db,
                include_stale=True,
                progress_callback=lambda progress: job.update(
                    detail=progress.get("summary", "Running…"), result=progress
                ),
            )

    return await start_background_job(
        request,
        user=user,
        database_url=database_url,
        kind="tag",
        key="pipeline:tag:stale",
        label="Retag stale events",
        runner=runner,
        target_id="dashboard-job-status",
    )
