"""Pipeline orchestration: scrape → tag → rank → notify."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from src.db.database import Database
from src.db.models import InterestProfile, User
from src.notifications.dispatcher import NotificationDispatcher
from src.notifications.formatter import format_console_message
from src.ranker.scoring import rank_events
from src.ranker.weather import WeatherService
from src.scrapers.allevents import AllEventsScraper
from src.scrapers.brec import BrecScraper
from src.scrapers.eventbrite import EventbriteScraper
from src.scrapers.generic import GenericScraper
from src.scrapers.lafayette import LafayetteScraper
from src.scrapers.library import LibraryScraper
from src.scrapers.recipe import ScrapeRecipe
from src.tagger.llm import EventTagger

ALL_SCRAPERS = [
    LafayetteScraper(),  # Lafayette-first: Moncus Park, Acadiana Arts, Science Museum
    EventbriteScraper(),  # Both cities
    AllEventsScraper(),  # Both cities
    BrecScraper(),  # Baton Rouge
    LibraryScraper(),  # Both cities (needs Playwright for full results)
]


async def run_scrape(db: Database | None = None) -> int:
    """Run all scrapers (built-in + user-defined) and store results."""
    own_db = db is None
    if own_db:
        db = Database()
        await db.connect()

    total = 0

    # 1. Built-in scrapers
    for scraper in ALL_SCRAPERS:
        try:
            print(f"\n{'=' * 40}")
            print(f"Scraping: {scraper.source_name}")
            print(f"{'=' * 40}")
            events = await scraper.scrape()
            for event in events:
                await db.upsert_event(event)
            total += len(events)
            print(f"  ✓ {len(events)} events from {scraper.source_name}")
        except Exception as e:
            print(f"  ✗ {scraper.source_name} error: {e}")

    # 2. User-defined sources
    sources = await db.get_enabled_sources()
    for source in sources:
        try:
            if not source.recipe_json:
                continue
            recipe = ScrapeRecipe.model_validate_json(source.recipe_json)
            scraper = GenericScraper(
                url=source.url,
                source_id=source.id,
                recipe=recipe,
            )
            print(f"\n{'=' * 40}")
            print(f"Scraping custom: {source.name}")
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


async def run_tag(db: Database | None = None) -> int:
    """Tag all untagged events with the LLM. Returns count tagged."""
    own_db = db is None
    if own_db:
        db = Database()
        await db.connect()

    untagged = await db.get_untagged_events()
    if not untagged:
        print("No untagged events found.")
        if own_db:
            await db.close()
        return 0

    print(f"Tagging {len(untagged)} events...")
    tagger = EventTagger()
    tagged = await tagger.tag_events(untagged)

    for event, tags in tagged:
        await db.update_event_tags(event.id, tags)

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

    # Resolve settings from user profile or defaults
    profile = user.interest_profile if user else InterestProfile()
    channels = user.notification_channels if user else ["console"]
    email_to = user.email_to if user else ""
    name = user.child_name if user else child_name

    # Find next weekend
    today = date.today()
    days_until_sat = (5 - today.weekday()) % 7
    if days_until_sat == 0 and datetime.now().hour >= 12:
        days_until_sat = 7  # if it's Saturday afternoon, look at next weekend
    saturday = today + timedelta(days=days_until_sat)
    sunday = saturday + timedelta(days=1)

    print(f"\nWeekend: {saturday} / {sunday}")

    # Get weather
    weather_svc = WeatherService()
    weather = await weather_svc.get_weekend_forecast(saturday, sunday)
    print(
        f"Weather: Sat {weather['saturday'].icon} {weather['saturday'].temp_high_f:.0f}°F / "
        f"Sun {weather['sunday'].icon} {weather['sunday'].temp_high_f:.0f}°F"
    )

    # Get weekend events
    events = await db.get_events_for_weekend(saturday.isoformat(), sunday.isoformat())
    print(f"Weekend events: {len(events)}")

    # If few weekend events, supplement with upcoming week
    if len(events) < 10:
        print("Few weekend events, adding upcoming week...")
        upcoming = await db.get_recent_events(days=14)
        existing_ids = {e.id for e in events}
        for e in upcoming:
            if e.id not in existing_ids:
                events.append(e)

    # Filter to tagged events only
    tagged_events = [e for e in events if e.tags is not None]
    print(f"Found {len(tagged_events)} tagged events for ranking.")

    if not tagged_events:
        msg = f"No events found for this weekend ({saturday} - {sunday}). Try running scrape + tag first."
        print(msg)
        if own_db:
            await db.close()
        return msg

    # Rank using user's interest profile
    ranked = rank_events(tagged_events, profile, weather)

    # Format message
    message = format_console_message(ranked, weather, name)

    # Dispatch to user's chosen channels
    dispatcher = NotificationDispatcher()
    results = await dispatcher.dispatch(message, channels=channels, email_to=email_to)
    print(f"Notification results: {results}")

    if own_db:
        await db.close()

    return message


async def run_full_pipeline(*, user: User | None = None) -> str:
    """Run the complete pipeline: scrape → tag → notify."""
    async with Database() as db:
        await run_scrape(db)
        await run_tag(db)
        return await run_notify(db, user=user)
