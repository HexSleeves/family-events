from __future__ import annotations

from pydantic import ValidationError

from src.cities import normalize_city_list as normalize_city_slug_list
from src.cities import normalize_city_slug
from src.db.models import Constraints, InterestProfile, User
from src.predefined_sources import list_predefined_sources, make_predefined_source


def normalize_city_list(text: str, *, fallback_home_city: str) -> list[str]:
    values = [item.strip() for item in text.split(",") if item.strip()]
    if fallback_home_city and normalize_city_slug(
        fallback_home_city
    ) not in normalize_city_slug_list(values):
        values.insert(0, fallback_home_city)
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = normalize_city_slug(value)
        if key == "unknown":
            continue
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped


def build_interest_profile_from_form(
    form, *, home_city: str, preferred_cities: list[str]
) -> InterestProfile:
    loves = [item.strip() for item in str(form.get("loves", "")).split(",") if item.strip()]
    likes = [item.strip() for item in str(form.get("likes", "")).split(",") if item.strip()]
    dislikes = [item.strip() for item in str(form.get("dislikes", "")).split(",") if item.strip()]
    favorite_categories = [
        item.strip() for item in str(form.get("favorite_categories", "")).split(",") if item.strip()
    ]
    avoid_categories = [
        item.strip() for item in str(form.get("avoid_categories", "")).split(",") if item.strip()
    ]
    return InterestProfile(
        child_age_years=int(str(form.get("child_age_years", "3") or "3")),
        child_age_months=int(str(form.get("child_age_months", "0") or "0")),
        temperament=str(form.get("temperament", "")).strip(),
        sensory_notes=str(form.get("sensory_notes", "")).strip(),
        accessibility_needs=str(form.get("accessibility_needs", "")).strip(),
        loves=loves,
        likes=likes,
        dislikes=dislikes,
        favorite_categories=favorite_categories,
        avoid_categories=avoid_categories,
        notes_for_recommendations=str(form.get("notes_for_recommendations", "")).strip(),
        constraints=Constraints(
            max_drive_time_minutes=int(str(form.get("max_drive", "45") or "45")),
            preferred_cities=preferred_cities,
            home_city=home_city,
            nap_time=str(form.get("nap_time", "13:00-15:00")).strip(),
            bedtime=str(form.get("bedtime", "19:30")).strip(),
            budget_per_event=float(str(form.get("budget", "30.0") or "30.0")),
        ),
    )


def validate_onboarding_form(form) -> list[str]:
    errors: list[str] = []
    if not str(form.get("home_city", "")).strip():
        errors.append("Home city is required.")
    if not str(form.get("child_name", "")).strip():
        errors.append("Child name is required.")
    if not str(form.get("temperament", "")).strip():
        errors.append("Tell us a bit about your child's temperament.")
    try:
        Constraints(
            nap_time=str(form.get("nap_time", "13:00-15:00")).strip(),
            bedtime=str(form.get("bedtime", "19:30")).strip(),
        )
    except ValidationError as exc:
        errors.extend(str(error["msg"]).removeprefix("Value error, ") for error in exc.errors())
    return errors


def recommended_source_keys_for_city(home_city: str) -> list[str]:
    city = home_city.strip().lower()
    return [item["key"] for item in list_predefined_sources(city=home_city)] if city else []


async def ensure_predefined_sources(db, *, user: User, source_keys: list[str]) -> None:
    for source_key in source_keys:
        source = make_predefined_source(user_id=user.id, source_key=source_key)
        existing = await db.get_user_source_by_url(user.id, source.url)
        if existing:
            continue
        await db.create_source(source)
