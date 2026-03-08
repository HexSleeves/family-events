from __future__ import annotations

from src.db.models import Source
from src.scrapers.router import extract_domain

PREDEFINED_SOURCE_CATALOG: list[dict[str, str]] = [
    {
        "key": "baton-rouge-brec",
        "city": "Baton Rouge",
        "category": "parks",
        "name": "BREC Parks",
        "url": "https://www.brec.org/calendar",
        "description": "Baton Rouge parks and recreation calendar",
    },
    {
        "key": "baton-rouge-eventbrite",
        "city": "Baton Rouge",
        "category": "aggregator",
        "name": "Eventbrite Baton Rouge Family",
        "url": "https://www.eventbrite.com/d/la--baton-rouge/family-events/",
        "description": "Family-friendly Eventbrite listings for Baton Rouge",
    },
    {
        "key": "baton-rouge-allevents",
        "city": "Baton Rouge",
        "category": "aggregator",
        "name": "AllEvents Baton Rouge Family",
        "url": "https://allevents.in/baton-rouge/family",
        "description": "AllEvents family listings for Baton Rouge",
    },
    {
        "key": "baton-rouge-library",
        "city": "Baton Rouge",
        "category": "library",
        "name": "East Baton Rouge Parish Library",
        "url": "https://ebrpl.libcal.com/rss.php",
        "description": "Library story times and kids programming",
    },
    {
        "key": "lafayette-moncus",
        "city": "Lafayette",
        "category": "park",
        "name": "Moncus Park",
        "url": "https://moncuspark.org/events/",
        "description": "Outdoor family events at Moncus Park",
    },
    {
        "key": "lafayette-aca",
        "city": "Lafayette",
        "category": "arts",
        "name": "Acadiana Center for the Arts",
        "url": "https://acadianacenterforthearts.org/events/",
        "description": "Arts and culture events in Lafayette",
    },
    {
        "key": "lafayette-science-museum",
        "city": "Lafayette",
        "category": "museum",
        "name": "Lafayette Science Museum",
        "url": "https://lafayettesciencemuseum.org/events",
        "description": "Science museum family events",
    },
    {
        "key": "lafayette-eventbrite",
        "city": "Lafayette",
        "category": "aggregator",
        "name": "Eventbrite Lafayette Family",
        "url": "https://www.eventbrite.com/d/la--lafayette/family-events/",
        "description": "Family-friendly Eventbrite listings for Lafayette",
    },
    {
        "key": "lafayette-allevents",
        "city": "Lafayette",
        "category": "aggregator",
        "name": "AllEvents Lafayette Family",
        "url": "https://allevents.in/lafayette/family",
        "description": "AllEvents family listings for Lafayette",
    },
    {
        "key": "lafayette-library",
        "city": "Lafayette",
        "category": "library",
        "name": "Lafayette Public Library",
        "url": "https://lafayettela.libcal.com/rss.php",
        "description": "Library story times and kids programming",
    },
    {
        "key": "new-orleans-eventbrite",
        "city": "New Orleans",
        "category": "aggregator",
        "name": "Eventbrite New Orleans Family",
        "url": "https://www.eventbrite.com/d/la--new-orleans/family-events/",
        "description": "Family-friendly Eventbrite listings for New Orleans",
    },
    {
        "key": "new-orleans-allevents",
        "city": "New Orleans",
        "category": "aggregator",
        "name": "AllEvents New Orleans Family",
        "url": "https://allevents.in/new-orleans/family",
        "description": "AllEvents family listings for New Orleans",
    },
    {
        "key": "houston-eventbrite",
        "city": "Houston",
        "category": "aggregator",
        "name": "Eventbrite Houston Family",
        "url": "https://www.eventbrite.com/d/tx--houston/family-events/",
        "description": "Family-friendly Eventbrite listings for Houston",
    },
    {
        "key": "houston-allevents",
        "city": "Houston",
        "category": "aggregator",
        "name": "AllEvents Houston Family",
        "url": "https://allevents.in/houston/family",
        "description": "AllEvents family listings for Houston",
    },
    {
        "key": "austin-eventbrite",
        "city": "Austin",
        "category": "aggregator",
        "name": "Eventbrite Austin Family",
        "url": "https://www.eventbrite.com/d/tx--austin/family-events/",
        "description": "Family-friendly Eventbrite listings for Austin",
    },
    {
        "key": "austin-allevents",
        "city": "Austin",
        "category": "aggregator",
        "name": "AllEvents Austin Family",
        "url": "https://allevents.in/austin/family",
        "description": "AllEvents family listings for Austin",
    },
    {
        "key": "dallas-eventbrite",
        "city": "Dallas",
        "category": "aggregator",
        "name": "Eventbrite Dallas Family",
        "url": "https://www.eventbrite.com/d/tx--dallas/family-events/",
        "description": "Family-friendly Eventbrite listings for Dallas",
    },
    {
        "key": "dallas-allevents",
        "city": "Dallas",
        "category": "aggregator",
        "name": "AllEvents Dallas Family",
        "url": "https://allevents.in/dallas/family",
        "description": "AllEvents family listings for Dallas",
    },
    {
        "key": "atlanta-eventbrite",
        "city": "Atlanta",
        "category": "aggregator",
        "name": "Eventbrite Atlanta Family",
        "url": "https://www.eventbrite.com/d/ga--atlanta/family-events/",
        "description": "Family-friendly Eventbrite listings for Atlanta",
    },
    {
        "key": "atlanta-allevents",
        "city": "Atlanta",
        "category": "aggregator",
        "name": "AllEvents Atlanta Family",
        "url": "https://allevents.in/atlanta/family",
        "description": "AllEvents family listings for Atlanta",
    },
]


def list_predefined_sources(*, city: str = "") -> list[dict[str, str]]:
    city_norm = city.strip().lower()
    if not city_norm:
        return PREDEFINED_SOURCE_CATALOG
    return [item for item in PREDEFINED_SOURCE_CATALOG if item["city"].lower() == city_norm]


def get_predefined_source(source_key: str) -> dict[str, str]:
    for item in PREDEFINED_SOURCE_CATALOG:
        if item["key"] == source_key:
            return item
    raise KeyError(source_key)


def make_predefined_source(*, user_id: str, source_key: str) -> Source:
    item = get_predefined_source(source_key)
    return Source(
        name=item["name"],
        url=item["url"],
        domain=extract_domain(item["url"]),
        city=item["city"],
        category=item["category"],
        user_id=user_id,
        builtin=True,
        status="active",
    )
