"""CLI entry point and FastAPI web server for Family Events."""

from __future__ import annotations

import argparse
import asyncio
import sys


def cli() -> None:
    parser = argparse.ArgumentParser(description="Family Events Discovery System")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("scrape", help="Run all scrapers")
    sub.add_parser("tag", help="Tag untagged events with LLM")
    notify_p = sub.add_parser("notify", help="Generate and send weekend notification")
    notify_p.add_argument("--name", default="Your Little One", help="Child's name")
    pipeline_p = sub.add_parser("pipeline", help="Run full pipeline: scrape + tag + notify")
    pipeline_p.add_argument("--name", default="Your Little One", help="Child's name")
    sub.add_parser("serve", help="Start the web server")
    sub.add_parser("events", help="List upcoming events")

    args = parser.parse_args()

    if args.command == "scrape":
        from src.scheduler import run_scrape
        asyncio.run(run_scrape())

    elif args.command == "tag":
        from src.scheduler import run_tag
        asyncio.run(run_tag())

    elif args.command == "notify":
        from src.scheduler import run_notify
        asyncio.run(run_notify(child_name=args.name))

    elif args.command == "pipeline":
        from src.scheduler import run_full_pipeline
        asyncio.run(run_full_pipeline(child_name=args.name))

    elif args.command == "serve":
        _serve()

    elif args.command == "events":
        asyncio.run(_list_events())

    else:
        parser.print_help()


async def _list_events() -> None:
    from src.db.database import Database
    async with Database() as db:
        events = await db.get_recent_events(days=30)
    if not events:
        print("No upcoming events. Run 'scrape' first.")
        return
    for e in events:
        tagged = "✅" if e.tags else "⬜"
        score = f"toddler={e.tags.toddler_score}" if e.tags else "untagged"
        print(f"{tagged} {e.start_time.strftime('%m/%d %a %-I%p')} | {e.title[:50]:50s} | {e.location_city:12s} | {e.source:12s} | {score}")
    print(f"\nTotal: {len(events)} events")


def _serve() -> None:
    import uvicorn
    from src.config import settings
    uvicorn.run(
        "src.web.app:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )


if __name__ == "__main__":
    cli()
