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
from src.utils import duration_ms, error_details, runtime_log

logger = logging.getLogger("uvicorn.error")
CRON_TZ = ZoneInfo("America/Chicago")


async def daily_scrape_and_tag() -> None:
    """Run at 2 AM daily: scrape all sources and tag new events."""
    started = time.perf_counter()
    runtime_log(logging.INFO, "cron_job_started", cron_job="daily_scrape_and_tag")
    try:
        async with create_database() as db:
            result = await run_scheduled_scrape_then_tag(db)
        runtime_log(
            logging.INFO,
            "cron_job_succeeded",
            cron_job="daily_scrape_and_tag",
            scraped=result.get("scraped", 0),
            tagged=result.get("tagged", 0),
            failed=result.get("failed", 0),
            duration_ms=duration_ms(started),
        )
    except Exception as exc:
        error_type, error_message = error_details(exc)
        runtime_log(
            logging.ERROR,
            "cron_job_failed",
            cron_job="daily_scrape_and_tag",
            error_type=error_type,
            error_message=error_message,
            duration_ms=duration_ms(started),
        )
        logger.exception("cron_job_failed_exception", extra={"cron_job": "daily_scrape_and_tag"})


async def friday_notification() -> None:
    """Run at 8 AM on Fridays: send weekend plans to each user."""
    started = time.perf_counter()
    runtime_log(logging.INFO, "cron_job_started", cron_job="friday_notification")
    try:
        async with create_database() as db:
            users = await db.get_all_users()
            if not users:
                runtime_log(
                    logging.INFO,
                    "cron_notification_no_users",
                    cron_job="friday_notification",
                )
                await run_notify(db)
            else:
                for user in users:
                    runtime_log(
                        logging.INFO,
                        "cron_notification_user_started",
                        cron_job="friday_notification",
                        user_id=user.id,
                        user_email=user.email,
                    )
                    try:
                        await run_notify(db, user=user)
                        runtime_log(
                            logging.INFO,
                            "cron_notification_user_succeeded",
                            cron_job="friday_notification",
                            user_id=user.id,
                            user_email=user.email,
                        )
                    except Exception as exc:
                        error_type, error_message = error_details(exc)
                        runtime_log(
                            logging.ERROR,
                            "cron_notification_user_failed",
                            cron_job="friday_notification",
                            user_id=user.id,
                            user_email=user.email,
                            error_type=error_type,
                            error_message=error_message,
                        )
                        logger.exception(
                            "cron_notification_user_failed_exception",
                            extra={
                                "cron_job": "friday_notification",
                                "user_id": user.id,
                                "user_email": user.email,
                            },
                        )
        runtime_log(
            logging.INFO,
            "cron_job_succeeded",
            cron_job="friday_notification",
            notified_user_count=len(users),
            duration_ms=duration_ms(started),
        )
    except Exception as exc:
        error_type, error_message = error_details(exc)
        runtime_log(
            logging.ERROR,
            "cron_job_failed",
            cron_job="friday_notification",
            error_type=error_type,
            error_message=error_message,
            duration_ms=duration_ms(started),
        )
        logger.exception("cron_job_failed_exception", extra={"cron_job": "friday_notification"})


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
    runtime_log(logging.INFO, "scheduler_started", timezone=CRON_TZ.key)
    for job in scheduler.get_jobs():
        runtime_log(
            logging.INFO,
            "scheduler_job_registered",
            scheduler_job_name=job.name,
            scheduler_job_id=job.id,
            next_run_time=str(job.next_run_time),
        )

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
