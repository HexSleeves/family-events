from __future__ import annotations

from src.cities import normalize_city_slug
from src.db.models import Source
from src.scrapers.router import extract_domain


def _eventbrite_source(key: str, city: str, state_slug: str, city_slug: str) -> dict[str, str]:
    return {
        "key": key,
        "city": city,
        "category": "aggregator",
        "name": f"Eventbrite {city} Family",
        "url": f"https://www.eventbrite.com/d/{state_slug}--{city_slug}/family-events/",
        "description": f"Family-friendly Eventbrite listings for {city}",
        "state_slug": state_slug,
        "city_slug": city_slug,
    }


def _allevents_source(key: str, city: str, city_slug: str) -> dict[str, str]:
    return {
        "key": key,
        "city": city,
        "category": "aggregator",
        "name": f"AllEvents {city} Family",
        "url": f"https://allevents.in/{city_slug}/family",
        "description": f"AllEvents family listings for {city}",
        "city_slug": city_slug,
    }


PREDEFINED_SOURCE_CATALOG: list[dict[str, str]] = [
    {
        "key": "baton-rouge-brec",
        "city": "Baton Rouge",
        "category": "parks",
        "name": "BREC Parks",
        "url": "https://www.brec.org/calendar",
        "description": "Baton Rouge parks and recreation calendar",
    },
    _eventbrite_source("baton-rouge-eventbrite", "Baton Rouge", "la", "baton-rouge"),
    _allevents_source("baton-rouge-allevents", "Baton Rouge", "baton-rouge"),
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
    _eventbrite_source("lafayette-eventbrite", "Lafayette", "la", "lafayette"),
    _allevents_source("lafayette-allevents", "Lafayette", "lafayette"),
    {
        "key": "lafayette-library",
        "city": "Lafayette",
        "category": "library",
        "name": "Lafayette Public Library",
        "url": "https://lafayettela.libcal.com/rss.php",
        "description": "Library story times and kids programming",
    },
    _eventbrite_source("new-orleans-eventbrite", "New Orleans", "la", "new-orleans"),
    _allevents_source("new-orleans-allevents", "New Orleans", "new-orleans"),
    _eventbrite_source("houston-eventbrite", "Houston", "tx", "houston"),
    _allevents_source("houston-allevents", "Houston", "houston"),
    _eventbrite_source("austin-eventbrite", "Austin", "tx", "austin"),
    _allevents_source("austin-allevents", "Austin", "austin"),
    _eventbrite_source("dallas-eventbrite", "Dallas", "tx", "dallas"),
    _allevents_source("dallas-allevents", "Dallas", "dallas"),
    _eventbrite_source("atlanta-eventbrite", "Atlanta", "ga", "atlanta"),
    _allevents_source("atlanta-allevents", "Atlanta", "atlanta"),
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
        city_slug=normalize_city_slug(item["city"]),
        category=item["category"],
        user_id=user_id,
        builtin=True,
        status="active",
    )
