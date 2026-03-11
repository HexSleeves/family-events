"""APScheduler-based cron for scraping and notifications.

Run with: uv run python -m src.cron
"""

from __future__ import annotations

import asyncio
import logging
import time
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.db.database import create_database
from src.scheduler import run_notify, run_scheduled_scrape_then_tag

logger = logging.getLogger("uvicorn.error")
CRON_TZ = ZoneInfo("America/Chicago")


async def daily_scrape_and_tag() -> None:
    """Run at 2 AM daily: scrape all sources and tag new events."""
    started = time.perf_counter()
    logger.info("cron_daily_scrape_tag_started")
    try:
        async with create_database() as db:
            result = await run_scheduled_scrape_then_tag(db)
        logger.info(
            "cron_daily_scrape_tag_succeeded duration_seconds=%.2f scraped=%s tagged=%s failed=%s",
            time.perf_counter() - started,
            result.get("scraped", 0),
            result.get("tagged", 0),
            result.get("failed", 0),
        )
    except Exception:
        logger.exception(
            "cron_daily_scrape_tag_failed duration_seconds=%.2f",
            time.perf_counter() - started,
        )


async def friday_notification() -> None:
    """Run at 8 AM on Fridays: send weekend plans to each user."""
    started = time.perf_counter()
    logger.info("cron_friday_notification_started")
    try:
        async with create_database() as db:
            users = await db.get_all_users()
            if not users:
                logger.info("cron_friday_notification_no_users")
                await run_notify(db)
            else:
                for user in users:
                    logger.info("cron_friday_notification_user email=%s", user.email)
                    try:
                        await run_notify(db, user=user)
                    except Exception:
                        logger.exception(
                            "cron_friday_notification_user_failed email=%s", user.email
                        )
        logger.info(
            "cron_friday_notification_succeeded duration_seconds=%.2f",
            time.perf_counter() - started,
        )
    except Exception:
        logger.exception(
            "cron_friday_notification_failed duration_seconds=%.2f",
            time.perf_counter() - started,
        )


async def main() -> None:
    scheduler = AsyncIOScheduler(timezone=CRON_TZ)

    scheduler.add_job(
        daily_scrape_and_tag,
        CronTrigger(hour=2, minute=0, timezone=CRON_TZ),
        id="daily_scrape",
        name="Daily scrape + tag",
    )

    scheduler.add_job(
        friday_notification,
        CronTrigger(day_of_week="fri", hour=8, minute=0, timezone=CRON_TZ),
        id="friday_notify",
        name="Friday notification",
    )

    scheduler.start()
    logger.info("scheduler_started timezone=%s", CRON_TZ.key)
    for job in scheduler.get_jobs():
        logger.info(
            "scheduler_job_registered name=%s next_run_time=%s", job.name, job.next_run_time
        )

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
