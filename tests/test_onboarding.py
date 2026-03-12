from __future__ import annotations

from src.cities import normalize_city_list as normalize_city_slug_list
from src.cities import normalize_city_slug, user_visible_city_slugs
from src.db.models import User
from src.onboarding import normalize_city_list
from src.predefined_sources import get_predefined_source, make_predefined_source
from src.scrapers.allevents import AllEventsScraper
from src.scrapers.eventbrite import EventbriteScraper
from src.scrapers.router import get_builtin_scraper
from src.tagger.llm import EventTagger


def test_normalize_city_list_includes_home_city_and_dedupes():
    cities = normalize_city_list(
        "Baton Rouge, Lafayette, baton rouge", fallback_home_city="Baton Rouge"
    )
    assert cities == ["Baton Rouge", "Lafayette"]


def test_normalize_city_slug_uses_exact_city_rules():
    assert normalize_city_slug(" Baton_Rouge!! ") == "baton-rouge"
    assert normalize_city_slug("San   Francisco") == "san-francisco"


def test_normalize_city_slug_list_dedupes_case_insensitively():
    assert normalize_city_slug_list(["Baton Rouge", "baton rouge", " Lafayette "]) == [
        "baton-rouge",
        "lafayette",
    ]


def test_user_visible_city_slugs_prefers_home_and_preferred():
    user = User(
        email="cities@example.com",
        display_name="Cities",
        password_hash="hash",
        home_city="Baton Rouge",
        preferred_cities=["Lafayette", "baton rouge"],
    )

    assert user_visible_city_slugs(user) == ["baton-rouge", "lafayette"]


def test_system_prompt_uses_child_profile():
    prompt = EventTagger()._system_prompt()
    assert "CHILD PROFILE" in prompt
    assert "Home city" in prompt


def test_predefined_eventbrite_source_carries_city_slug_and_state_slug():
    source = get_predefined_source("houston-eventbrite")
    assert source["state_slug"] == "tx"
    assert source["city_slug"] == "houston"


def test_builtin_router_builds_parameterized_eventbrite_scraper():
    source = make_predefined_source(user_id="user-1", source_key="houston-eventbrite")
    scraper = get_builtin_scraper(source)
    assert isinstance(scraper, EventbriteScraper)
    assert scraper.state_slug == "tx"
    assert scraper.city_slug == "houston"
    assert scraper.city == "Houston"


def test_builtin_router_builds_parameterized_allevents_scraper():
    source = make_predefined_source(user_id="user-1", source_key="new-orleans-allevents")
    scraper = get_builtin_scraper(source)
    assert isinstance(scraper, AllEventsScraper)
    assert scraper.city_slug == "new-orleans"
    assert scraper.category_slug == "family"
    assert scraper.city == "New Orleans"


def test_validate_onboarding_form_rejects_invalid_schedule_fields():
    from src.onboarding import validate_onboarding_form

    errors = validate_onboarding_form(
        {
            "home_city": "Lafayette",
            "child_name": "Em",
            "temperament": "curious",
            "nap_time": "",
            "bedtime": "bad",
        }
    )

    assert errors == ["bedtime must use HH:MM"]
