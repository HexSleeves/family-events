"""Pipeline orchestration: scrape → tag → rank → notify."""

from __future__ import annotations

import logging
import time

from src.cities import user_visible_city_slugs
from src.config import settings
from src.db.database import Database, create_database
from src.db.models import InterestProfile, Job, Source, User
from src.notifications.dispatcher import NotificationDispatcher
from src.notifications.formatter import format_console_message
from src.ranker.scoring import rank_events, score_event_breakdown
from src.ranker.weather import WeatherService
from src.scrapers.generic import GenericScraper
from src.scrapers.recipe import ScrapeRecipe
from src.scrapers.router import get_builtin_scraper
from src.tagger.llm import EventTagger
from src.tagger.taxonomy import TAGGING_VERSION
from src.timezones import current_weekend_dates, local_today, utc_now
from src.web.auth import hash_password

SYSTEM_USER_EMAIL = "system@family-events.local"
SYSTEM_USER_DISPLAY_NAME = "System"
logger = logging.getLogger("uvicorn.error")


def _duration_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def _runtime_log(level: int, event: str, **context: object) -> None:
    logger.log(
        level,
        event,
        extra={key: value for key, value in context.items() if value is not None},
    )


def _error_details(exc: Exception) -> tuple[str, str]:
    message = str(exc).strip() or repr(exc)
    return type(exc).__name__, message


async def run_scrape(db: Database | None = None) -> int:
    """Run all scrapers (built-in + user-defined) and store results."""
    started = time.perf_counter()
    own_db = db is None
    if own_db:
        db = create_database()
        await db.connect()

    total = 0
    all_sources = await db.get_all_sources()
    enabled_sources = [source for source in all_sources if source.enabled]
    _runtime_log(
        logging.INFO,
        "pipeline_stage_started",
        stage="scrape",
        source_count=len(enabled_sources),
    )

    for source in all_sources:
        if not source.enabled:
            continue
        source_started = time.perf_counter()
        try:
            scraper = _build_scraper(source)
            _runtime_log(
                logging.INFO,
                "pipeline_scrape_source_started",
                stage="scrape",
                source_id=source.id,
                source_name=source.name,
                source_url=source.url,
                builtin=source.builtin,
                scraper_class=type(scraper).__name__,
            )
            events = await scraper.scrape()
            for event in events:
                await db.upsert_event(event)
            await db.update_source_status(source.id, count=len(events))
            total += len(events)
            _runtime_log(
                logging.INFO,
                "pipeline_scrape_source_succeeded",
                stage="scrape",
                source_id=source.id,
                source_name=source.name,
                source_url=source.url,
                event_count=len(events),
                duration_ms=_duration_ms(source_started),
            )
        except Exception as exc:
            error_type, error_message = _error_details(exc)
            _runtime_log(
                logging.ERROR,
                "pipeline_scrape_source_failed",
                stage="scrape",
                source_id=source.id,
                source_name=source.name,
                source_url=source.url,
                error_type=error_type,
                error_message=error_message,
                duration_ms=_duration_ms(source_started),
            )
            await db.update_source_status(source.id, error=error_message)

    if own_db:
        await db.close()

    _runtime_log(
        logging.INFO,
        "pipeline_stage_succeeded",
        stage="scrape",
        source_count=len(enabled_sources),
        scraped=total,
        duration_ms=_duration_ms(started),
    )
    return total


async def ensure_system_user(db: Database) -> User:
    """Ensure scheduled/system jobs have a durable synthetic owner."""
    existing = await db.get_user_by_email(SYSTEM_USER_EMAIL)
    if existing:
        return existing

    user = User(
        email=SYSTEM_USER_EMAIL,
        display_name=SYSTEM_USER_DISPLAY_NAME,
        password_hash=hash_password("system-user-password-disabled"),
        onboarding_complete=True,
        notification_channels=["console"],
    )
    await db.create_user(user)
    created = await db.get_user_by_email(SYSTEM_USER_EMAIL)
    assert created is not None
    return created


def _build_scraper(source: Source):
    if source.builtin:
        scraper = get_builtin_scraper(source)
        if scraper is None:
            raise ValueError(f"No built-in scraper for {source.url}")
        return scraper
    if not source.recipe_json:
        raise ValueError(f"Custom source missing recipe: {source.url}")
    recipe = ScrapeRecipe.model_validate_json(source.recipe_json)
    return GenericScraper(url=source.url, source_id=source.id, recipe=recipe)


async def run_tag(
    db: Database | None = None,
    *,
    progress_callback=None,
    include_stale: bool = True,
) -> int:
    """Tag all untagged events with the LLM. Returns count tagged."""
    started = time.perf_counter()
    own_db = db is None
    if own_db:
        db = create_database()
        await db.connect()

    try:
        untagged = await db.get_untagged_events(
            tagging_version=TAGGING_VERSION,
            include_stale=include_stale,
        )
        tagger = EventTagger()
        total = len(untagged)
        _runtime_log(
            logging.INFO,
            "pipeline_stage_started",
            stage="tag",
            total=total,
            include_stale=include_stale,
            tagger_model=tagger.model,
            timeout_seconds=settings.openai_timeout_seconds if tagger.model != "heuristic" else 0,
            concurrency=settings.tagger_concurrency,
            batch_size=settings.tagger_batch_size,
        )
        if not untagged:
            if progress_callback is not None:
                await progress_callback({"processed": 0, "total": 0, "succeeded": 0, "failed": 0})
            _runtime_log(
                logging.INFO,
                "pipeline_stage_succeeded",
                stage="tag",
                total=0,
                succeeded=0,
                failed=0,
                duration_ms=_duration_ms(started),
            )
            return 0

        processed = 0
        succeeded = 0

        async def on_batch_complete(start_idx, batch, tagged_batch, _all_results):
            nonlocal processed, succeeded
            today = local_today()
            weather_stub = await WeatherService().get_weekend_forecast(today, today)
            for event, tags in tagged_batch:
                event.tags = tags
                breakdown = score_event_breakdown(event, InterestProfile(), weather_stub)
                score_breakdown = {
                    "final": breakdown.final,
                    "toddler_fit": breakdown.toddler_fit,
                    "intrinsic": breakdown.intrinsic,
                    "interest": breakdown.interest,
                    "weather": breakdown.weather,
                    "timing": breakdown.timing,
                    "logistics": breakdown.logistics,
                    "novelty": breakdown.novelty,
                    "city": breakdown.city,
                    "confidence": breakdown.confidence,
                    "rule_penalty": breakdown.rule_penalty,
                    "budget_penalty": breakdown.budget_penalty,
                }
                await db.update_event_tags(event.id, tags, score_breakdown=score_breakdown)
            processed = min(total, start_idx + len(batch))
            succeeded += len(tagged_batch)
            failed = processed - succeeded
            _runtime_log(
                logging.INFO,
                "pipeline_tag_progress",
                stage="tag",
                processed=processed,
                total=total,
                succeeded=succeeded,
                failed=failed,
            )
            if progress_callback is not None:
                await progress_callback(
                    {
                        "processed": processed,
                        "total": total,
                        "succeeded": succeeded,
                        "failed": failed,
                        "summary": (
                            f"{processed}/{total} processed · "
                            f"{succeeded} tagged · {failed} failed"
                        ),
                    }
                )

        tagged = await tagger.tag_events_in_batches(
            untagged,
            batch_size=max(1, settings.tagger_batch_size),
            on_batch_complete=on_batch_complete,
        )
        failed = total - len(tagged)
        _runtime_log(
            logging.INFO,
            "pipeline_stage_succeeded",
            stage="tag",
            total=total,
            succeeded=len(tagged),
            failed=failed,
            duration_ms=_duration_ms(started),
        )
        return len(tagged)
    except Exception as exc:
        error_type, error_message = _error_details(exc)
        _runtime_log(
            logging.ERROR,
            "pipeline_stage_failed",
            stage="tag",
            error_type=error_type,
            error_message=error_message,
            duration_ms=_duration_ms(started),
        )
        raise
    finally:
        if own_db:
            await db.close()


async def run_scrape_then_tag(
    db: Database | None = None,
    *,
    progress_callback=None,
    include_stale: bool = False,
) -> dict[str, int | str]:
    """Run the normal ingestion pipeline: scrape first, then tag."""
    started = time.perf_counter()
    own_db = db is None
    if own_db:
        db = create_database()
        await db.connect()

    current_stage = "scrape"
    _runtime_log(logging.INFO, "pipeline_run_started", include_stale=include_stale)
    try:
        if progress_callback is not None:
            await progress_callback(
                {
                    "phase": "scrape",
                    "processed": 0,
                    "total": 2,
                    "summary": "Scraping sources…",
                }
            )
        scraped = await run_scrape(db)
        _runtime_log(logging.INFO, "pipeline_stage_checkpoint", stage="scrape", scraped=scraped)

        if progress_callback is not None:
            await progress_callback(
                {
                    "phase": "tag",
                    "processed": 1,
                    "total": 2,
                    "scraped": scraped,
                    "summary": f"Scrape finished · {scraped} events scraped · tagging next",
                }
            )
        current_stage = "tag"
        tagged = await run_tag(db, progress_callback=progress_callback, include_stale=include_stale)
        failed = max(0, scraped - tagged)
        result = {
            "scraped": scraped,
            "tagged": tagged,
            "failed": failed,
            "summary": f"{scraped} events scraped · {tagged} tagged · {failed} failed",
        }
        _runtime_log(
            logging.INFO,
            "pipeline_run_succeeded",
            scraped=scraped,
            tagged=tagged,
            failed=failed,
            duration_ms=_duration_ms(started),
        )
        if progress_callback is not None:
            await progress_callback(result)
        return result
    except Exception as exc:
        error_type, error_message = _error_details(exc)
        _runtime_log(
            logging.ERROR,
            "pipeline_run_failed",
            stage=current_stage,
            error_type=error_type,
            error_message=error_message,
            duration_ms=_duration_ms(started),
        )
        raise
    finally:
        if own_db:
            await db.close()


async def run_notify(
    db: Database | None = None,
    *,
    user: User | None = None,
    child_name: str = "Your Little One",
) -> dict[str, object]:
    """Rank weekend events and send notification.

    If a User is provided, uses their profile for ranking and their
    notification_channels/email_to for dispatch.  Otherwise falls back
    to defaults (console-only, generic InterestProfile).
    """
    started = time.perf_counter()
    own_db = db is None
    if own_db:
        db = create_database()
        await db.connect()

    try:
        profile = user.interest_profile if user else InterestProfile()
        channels = user.notification_channels if user else ["console"]
        email_to = user.email_to if user else ""
        sms_to = user.sms_to if user else ""
        name = user.child_name if user else child_name
        _runtime_log(
            logging.INFO,
            "notify_run_started",
            stage="notify",
            user_id=user.id if user else None,
            user_email=user.email if user else None,
            channel_count=len(channels),
        )

        saturday, sunday = current_weekend_dates(roll_after_saturday_noon=True)
        weather_svc = WeatherService()
        weather = await weather_svc.get_weekend_forecast(saturday, sunday)

        visible_city_slugs = user_visible_city_slugs(user) if user else None
        events = await db.get_events_for_weekend(
            saturday.isoformat(),
            sunday.isoformat(),
            viewer_user_id=user.id if user else None,
            visible_city_slugs=visible_city_slugs or None,
        )

        if len(events) < 10:
            upcoming = await db.get_recent_events(
                days=14,
                viewer_user_id=user.id if user else None,
                visible_city_slugs=visible_city_slugs or None,
            )
            existing_ids = {e.id for e in events}
            for event in upcoming:
                if event.id not in existing_ids:
                    events.append(event)

        tagged_events = [event for event in events if event.tags is not None]

        if not tagged_events:
            msg = (
                f"No events found for this weekend ({saturday} - {sunday}). "
                "Try running scrape + tag first."
            )
            result: dict[str, object] = {
                "summary": msg,
                "message": msg,
                "results": [],
                "weekend_event_count": len(events),
                "ranked_event_count": 0,
            }
            _runtime_log(
                logging.INFO,
                "notify_run_succeeded",
                stage="notify",
                user_id=user.id if user else None,
                user_email=user.email if user else None,
                weekend_event_count=len(events),
                ranked_event_count=0,
                delivery_attempt_count=0,
                delivery_success_count=0,
                delivery_failure_count=0,
                duration_ms=_duration_ms(started),
            )
            return result

        ranked = rank_events(tagged_events, profile, weather)
        message = format_console_message(ranked, weather, name)

        dispatcher = NotificationDispatcher()
        results = await dispatcher.dispatch(
            message,
            channels=channels,
            email_to=email_to,
            sms_to=sms_to,
        )
        delivery_success_count = sum(1 for item in results if item["success"])
        delivery_failure_count = len(results) - delivery_success_count

        result = {
            "summary": f"{delivery_success_count}/{len(results)} deliveries succeeded",
            "message": message,
            "results": results,
            "weekend_event_count": len(events),
            "ranked_event_count": len(ranked),
        }
        _runtime_log(
            logging.INFO,
            "notify_run_succeeded",
            stage="notify",
            user_id=user.id if user else None,
            user_email=user.email if user else None,
            weekend_event_count=len(events),
            ranked_event_count=len(ranked),
            delivery_attempt_count=len(results),
            delivery_success_count=delivery_success_count,
            delivery_failure_count=delivery_failure_count,
            duration_ms=_duration_ms(started),
        )
        return result
    except Exception as exc:
        error_type, error_message = _error_details(exc)
        _runtime_log(
            logging.ERROR,
            "notify_run_failed",
            stage="notify",
            user_id=user.id if user else None,
            user_email=user.email if user else None,
            error_type=error_type,
            error_message=error_message,
            duration_ms=_duration_ms(started),
        )
        raise
    finally:
        if own_db:
            await db.close()


async def create_scheduled_job(
    db: Database,
    *,
    kind: str,
    job_key: str,
    label: str,
    detail: str = "Queued",
) -> str:
    """Create a persisted scheduled/system job record."""
    system_user = await ensure_system_user(db)

    job = Job(
        kind=kind,
        job_key=job_key,
        label=label,
        owner_user_id=system_user.id,
        state="running",
        detail=detail,
        started_at=utc_now(),
    )
    await db.create_job(job)
    return job.id


async def run_scheduled_scrape_then_tag(db: Database) -> dict[str, int | str]:
    """Run the scheduled scrape+tag pipeline and persist it as a job."""
    started = time.perf_counter()
    job_id = await create_scheduled_job(
        db,
        kind="pipeline",
        job_key="scheduled:pipeline:scrape-tag",
        label="Scheduled scrape + tag job",
        detail="Scraping sources…",
    )
    job_key = "scheduled:pipeline:scrape-tag"
    _runtime_log(
        logging.INFO,
        "scheduled_job_started",
        job_id=job_id,
        job_key=job_key,
        stage="pipeline",
    )
    try:
        result = await run_scrape_then_tag(
            db,
            include_stale=False,
            progress_callback=lambda progress: update_scheduled_job(
                db,
                job_id,
                detail=progress.get("summary", "Running…"),
                result=progress,
            ),
        )
        await update_scheduled_job(
            db, job_id, state="succeeded", detail="Completed", result=result, error=""
        )
        _runtime_log(
            logging.INFO,
            "scheduled_job_succeeded",
            job_id=job_id,
            job_key=job_key,
            stage="pipeline",
            scraped=result.get("scraped"),
            tagged=result.get("tagged"),
            failed=result.get("failed"),
            duration_ms=_duration_ms(started),
        )
        return result
    except Exception as exc:
        error_type, error_message = _error_details(exc)
        await update_scheduled_job(
            db, job_id, state="failed", detail="Failed", error=error_message
        )
        _runtime_log(
            logging.ERROR,
            "scheduled_job_failed",
            job_id=job_id,
            job_key=job_key,
            stage="pipeline",
            error_type=error_type,
            error_message=error_message,
            duration_ms=_duration_ms(started),
        )
        raise


async def update_scheduled_job(
    db: Database,
    job_id: str,
    *,
    state: str | None = None,
    detail: str | None = None,
    result: object | None = None,
    error: str | None = None,
) -> None:
    """Update a persisted scheduled/system job."""
    fields: dict[str, object] = {}
    if state is not None:
        fields["state"] = state
    if detail is not None:
        fields["detail"] = detail
    if result is not None:
        import json

        fields["result_json"] = json.dumps(result)
    if error is not None:
        fields["error"] = error
    if state in {"succeeded", "failed", "cancelled"}:
        fields["finished_at"] = utc_now()
    await db.update_job(job_id, **fields)
