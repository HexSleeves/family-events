"""Pipeline orchestration: scrape → tag → rank → notify."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from src.config import settings
from src.db.database import Database
from src.db.models import InterestProfile, Source, User
from src.notifications.dispatcher import NotificationDispatcher
from src.notifications.formatter import format_console_message
from src.ranker.scoring import rank_events, score_event_breakdown
from src.ranker.weather import WeatherService
from src.scrapers.generic import GenericScraper
from src.scrapers.recipe import ScrapeRecipe
from src.scrapers.router import get_builtin_scraper
from src.tagger.llm import EventTagger
from src.tagger.taxonomy import TAGGING_VERSION


async def run_scrape(db: Database | None = None) -> int:
    """Run all scrapers (built-in + user-defined) and store results."""
    own_db = db is None
    if own_db:
        db = Database()
        await db.connect()

    total = 0
    all_sources = await db.get_all_sources()

    for source in all_sources:
        if not source.enabled:
            continue
        try:
            scraper = _build_scraper(source)
            print(f"\n{'=' * 40}")
            print(f"Scraping: {source.name}")
            print(f"{'=' * 40}")
            events = await scraper.scrape()
            for event in events:
                await db.upsert_event(event)
            await db.update_source_status(source.id, count=len(events))
            total += len(events)
            print(f"  ✓ {len(events)} events from {source.name}")
        except Exception as e:
            print(f"  ✗ {source.name} error: {e}")
            await db.update_source_status(source.id, error=str(e))

    if own_db:
        await db.close()

    print(f"\nTotal events upserted: {total}")
    return total


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
    own_db = db is None
    if own_db:
        db = Database()
        await db.connect()

    untagged = await db.get_untagged_events(
        tagging_version=TAGGING_VERSION,
        include_stale=include_stale,
    )
    if not untagged:
        print("No untagged events found.")
        if progress_callback is not None:
            await progress_callback({"processed": 0, "total": 0, "succeeded": 0, "failed": 0})
        if own_db:
            await db.close()
        return 0

    total = len(untagged)
    print(f"Tagging {total} events...")
    tagger = EventTagger()
    if tagger.model != "heuristic":
        print(
            f"Using OpenAI model={tagger.model} timeout={settings.openai_timeout_seconds}s "
            f"concurrency={settings.tagger_concurrency} batch_size={settings.tagger_batch_size}"
        )
    else:
        print("Using heuristic tagger (no OpenAI API key configured)")

    processed = 0
    succeeded = 0

    async def on_batch_complete(start_idx, batch, tagged_batch, _all_results):
        nonlocal processed, succeeded
        weather_stub = await WeatherService().get_weekend_forecast(date.today(), date.today())
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
        print(f"Progress: {processed}/{total} processed, {succeeded} tagged, {failed} failed")
        if progress_callback is not None:
            await progress_callback(
                {
                    "processed": processed,
                    "total": total,
                    "succeeded": succeeded,
                    "failed": failed,
                    "summary": f"{processed}/{total} processed · {succeeded} tagged · {failed} failed",
                }
            )

    tagged = await tagger.tag_events_in_batches(
        untagged,
        batch_size=max(1, settings.tagger_batch_size),
        on_batch_complete=on_batch_complete,
    )

    if own_db:
        await db.close()

    print(f"Tagged {len(tagged)} events.")
    return len(tagged)


async def run_notify(
    db: Database | None = None,
    *,
    user: User | None = None,
    child_name: str = "Your Little One",
) -> str:
    """Rank weekend events and send notification.

    If a User is provided, uses their profile for ranking and their
    notification_channels/email_to for dispatch.  Otherwise falls back
    to defaults (console-only, generic InterestProfile).
    """
    own_db = db is None
    if own_db:
        db = Database()
        await db.connect()

    profile = user.interest_profile if user else InterestProfile()
    channels = user.notification_channels if user else ["console"]
    email_to = user.email_to if user else ""
    sms_to = user.sms_to if user else ""
    name = user.child_name if user else child_name

    today = date.today()
    days_until_sat = (5 - today.weekday()) % 7
    if days_until_sat == 0 and datetime.now().hour >= 12:
        days_until_sat = 7
    saturday = today + timedelta(days=days_until_sat)
    sunday = saturday + timedelta(days=1)

    print(f"\nWeekend: {saturday} / {sunday}")

    weather_svc = WeatherService()
    weather = await weather_svc.get_weekend_forecast(saturday, sunday)
    print(
        f"Weather: Sat {weather['saturday'].icon} {weather['saturday'].temp_high_f:.0f}°F / "
        f"Sun {weather['sunday'].icon} {weather['sunday'].temp_high_f:.0f}°F"
    )

    events = await db.get_events_for_weekend(saturday.isoformat(), sunday.isoformat())
    print(f"Weekend events: {len(events)}")

    if len(events) < 10:
        print("Few weekend events, adding upcoming week...")
        upcoming = await db.get_recent_events(days=14)
        existing_ids = {e.id for e in events}
        for e in upcoming:
            if e.id not in existing_ids:
                events.append(e)

    tagged_events = [e for e in events if e.tags is not None]
    print(f"Found {len(tagged_events)} tagged events for ranking.")

    if not tagged_events:
        msg = f"No events found for this weekend ({saturday} - {sunday}). Try running scrape + tag first."
        print(msg)
        if own_db:
            await db.close()
        return msg

    ranked = rank_events(tagged_events, profile, weather)
    message = format_console_message(ranked, weather, name)

    dispatcher = NotificationDispatcher()
    results = await dispatcher.dispatch(
        message,
        channels=channels,
        email_to=email_to,
        sms_to=sms_to,
    )
    print(f"Notification results: {results}")

    if own_db:
        await db.close()

    return message
