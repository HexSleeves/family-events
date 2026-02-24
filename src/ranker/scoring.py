"""Event ranking and scoring based on toddler-friendliness, interests, weather, and timing."""

from __future__ import annotations

from src.db.models import Event, EventTags, InterestProfile
from src.ranker.weather import DayForecast


def score_event(
    event: Event,
    profile: InterestProfile,
    weather: dict[str, DayForecast],
) -> float:
    """Score an event based on toddler-friendliness, interests, weather, timing."""
    if not event.tags:
        return 0.0

    tags = event.tags

    # 1. Toddler score (0-10, weight 3.0)
    toddler = tags.toddler_score * 3.0

    # 2. Interest match (weight 2.5)
    interest = _interest_score(tags.categories, profile) * 2.5

    # 3. Weather compatibility (weight 2.0)
    weather_pts = _weather_score(event, tags, weather) * 2.0

    # 4. Timing score (weight 1.5)
    timing = _timing_score(event, profile) * 1.5

    # 5. Logistics score (weight 1.0)
    logistics = _logistics_score(tags) * 1.0

    # 6. Novelty bonus (weight 0.5)
    novelty = (5.0 if not event.attended else 0.0) * 0.5

    # Budget filter
    if (
        not event.is_free
        and event.price_min is not None
        and event.price_min > profile.constraints.budget_per_event
    ):
        return 0.0

    # 7. City/proximity score (weight 2.0) — strongly prefer home city
    city_pts = _city_score(event, profile) * 2.0

    return toddler + interest + weather_pts + timing + logistics + novelty + city_pts


# ---------------------------------------------------------------------------
# Sub-scores
# ---------------------------------------------------------------------------

_CAT_TO_INTEREST: dict[str, str] = {
    "animals": "animals",
    "arts": "art_messy",
    "music": "music",
    "nature": "nature_walks",
    "learning": "story_time",
    "play": "playground",
    "sports": "playground",
    "water": "water_play",
}


def _interest_score(categories: list[str], profile: InterestProfile) -> float:
    score = 0.0
    for cat in categories:
        interest = _CAT_TO_INTEREST.get(cat, cat)
        if interest in profile.loves:
            score += 10.0
        elif interest in profile.likes:
            score += 5.0
    return min(score, 30.0)  # cap


def _weather_score(
    event: Event,
    tags: EventTags,
    weather: dict[str, DayForecast],
) -> float:
    day_key = "saturday" if event.start_time.weekday() == 5 else "sunday"
    forecast = weather.get(day_key)
    if not forecast:
        return 5.0

    score = 5.0

    # Rain check
    if forecast.precipitation_pct > 50:
        if tags.indoor_outdoor == "indoor" or tags.good_for_rain:
            score += 5.0  # great indoor option on rainy day
        elif tags.indoor_outdoor == "outdoor" and tags.weather_dependent:
            score -= 5.0

    # Heat check (Louisiana summer!)
    if forecast.temp_high_f > 95:
        if tags.indoor_outdoor == "indoor" or tags.good_for_heat:
            score += 3.0
        elif tags.indoor_outdoor == "outdoor":
            if event.start_time.hour < 11:
                score += 1.0  # morning events are tolerable
            else:
                score -= 4.0

    # Nice weather bonus for outdoor events
    if 65 < forecast.temp_high_f < 85 and forecast.precipitation_pct < 30:
        if tags.indoor_outdoor in ("outdoor", "both"):
            score += 3.0

    return max(score, 0.0)


def _timing_score(event: Event, profile: InterestProfile) -> float:
    score = 5.0
    hour = event.start_time.hour

    nap_start = profile.constraints.nap_start
    nap_end = profile.constraints.nap_end
    bedtime = profile.constraints.bedtime_time

    event_time = event.start_time.time()

    # Nap overlap penalty
    if nap_start <= event_time <= nap_end:
        score -= 5.0

    # After bedtime penalty
    if event_time >= bedtime:
        score -= 10.0

    # Morning sweet spot bonus (9-11am)
    if 9 <= hour <= 11:
        score += 5.0
    elif 11 < hour < 13:
        score += 2.0

    return max(score, 0.0)


def _logistics_score(tags: EventTags) -> float:
    score = 0.0
    if tags.stroller_friendly:
        score += 2.0
    if tags.parking_available:
        score += 1.5
    if tags.bathroom_accessible:
        score += 2.0
    if tags.nap_compatible:
        score += 2.0
    if tags.food_available:
        score += 1.0
    if tags.meltdown_risk == "low":
        score += 2.0
    elif tags.meltdown_risk == "high":
        score -= 2.0
    return score


def _city_score(event: Event, profile: InterestProfile) -> float:
    """Boost events in the user's home city, penalize far-away ones."""
    home = profile.constraints.home_city
    if event.location_city == home:
        return 10.0  # Strong boost for home city
    elif event.location_city in profile.constraints.preferred_cities:
        return 2.0   # Acceptable but not preferred
    else:
        return -5.0  # Unknown/far city


# ---------------------------------------------------------------------------
# Public ranking function
# ---------------------------------------------------------------------------


def rank_events(
    events: list[Event],
    profile: InterestProfile,
    weather: dict[str, DayForecast],
) -> list[tuple[Event, float]]:
    """Rank events by score, return sorted list of (event, score)."""
    scored = [(e, score_event(e, profile, weather)) for e in events if e.tags]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored
