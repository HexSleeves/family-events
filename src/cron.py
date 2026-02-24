"""APScheduler-based cron for scraping and notifications.

Run with: uv run python -m src.cron
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.scheduler import run_notify, run_scrape, run_tag


async def daily_scrape_and_tag():
    """Run at 2 AM daily: scrape all sources and tag new events."""
    print(f"\n{'=' * 60}")
    print(f"[CRON] Daily scrape started at {datetime.now()}")
    print(f"{'=' * 60}")
    try:
        await run_scrape()
        await run_tag()
    except Exception as e:
        print(f"[CRON] Scrape/tag error: {e}")


async def friday_notification():
    """Run at 8 AM on Fridays: send weekend plans notification."""
    print(f"\n{'=' * 60}")
    print(f"[CRON] Friday notification at {datetime.now()}")
    print(f"{'=' * 60}")
    try:
        await run_notify()
    except Exception as e:
        print(f"[CRON] Notification error: {e}")


def main():
    scheduler = AsyncIOScheduler()

    # Daily at 2 AM Central
    scheduler.add_job(
        daily_scrape_and_tag,
        CronTrigger(hour=2, minute=0),
        id="daily_scrape",
        name="Daily scrape + tag",
    )

    # Friday at 8 AM Central
    scheduler.add_job(
        friday_notification,
        CronTrigger(day_of_week="fri", hour=8, minute=0),
        id="friday_notify",
        name="Friday notification",
    )

    scheduler.start()
    print("Scheduler started. Jobs:")
    for job in scheduler.get_jobs():
        print(f"  {job.name}: next run at {job.next_run_time}")

    # Keep running
    try:
        asyncio.get_event_loop().run_forever()
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


if __name__ == "__main__":
    main()
