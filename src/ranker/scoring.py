"""Event ranking and scoring based on toddler-friendliness, interests, weather, and timing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.db.models import Event, EventTags, InterestProfile
    from src.ranker.weather import DayForecast

RULE_SCORE_WEIGHT = 0.35
TODDLER_SCORE_WEIGHT = 2.2
INTEREST_WEIGHT = 1.4
WEATHER_WEIGHT = 1.0
TIMING_WEIGHT = 1.0
LOGISTICS_WEIGHT = 0.9
NOVELTY_WEIGHT = 0.4
CITY_WEIGHT = 0.8
CONFIDENCE_WEIGHT = 0.5


@dataclass(slots=True)
class ScoreBreakdown:
    final: float
    toddler_fit: float
    intrinsic: float
    interest: float
    weather: float
    timing: float
    logistics: float
    novelty: float
    city: float
    confidence: float
    budget_penalty: float
    rule_penalty: float


def score_event(
    event: Event,
    profile: InterestProfile,
    weather: dict[str, DayForecast],
) -> float:
    """Score an event based on toddler-friendliness, interests, weather, timing."""
    return score_event_breakdown(event, profile, weather).final


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


def _normalize_to_ten(value: float, *, max_value: float) -> float:
    if max_value <= 0:
        return 0.0
    return max(0.0, min(10.0, (value / max_value) * 10.0))


def _rule_penalty(tags: EventTags) -> float:
    penalty = 0.0
    if tags.audience == "adult_skewed":
        penalty += 4.0
    penalty += min(3.0, len(tags.exclusion_signals) * 1.5)
    if tags.meltdown_risk == "high":
        penalty += 2.0
    return penalty


def _confidence_bonus(tags: EventTags) -> float:
    return tags.confidence_score * 10.0


def score_event_breakdown(
    event: Event,
    profile: InterestProfile,
    weather: dict[str, DayForecast],
) -> ScoreBreakdown:
    if not event.tags:
        return ScoreBreakdown(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    tags = event.tags

    toddler_fit = tags.toddler_score * TODDLER_SCORE_WEIGHT
    intrinsic = (tags.raw_rule_score / 10.0) * RULE_SCORE_WEIGHT
    interest = _interest_score(tags.categories, profile) * INTEREST_WEIGHT
    weather_pts = _weather_score(event, tags, weather) * WEATHER_WEIGHT
    timing = _timing_score(event, profile, tags) * TIMING_WEIGHT
    logistics = _logistics_score(tags) * LOGISTICS_WEIGHT
    novelty = (5.0 if not event.attended else 0.0) * NOVELTY_WEIGHT
    city_pts = _city_score(event, profile) * CITY_WEIGHT
    confidence = _confidence_bonus(tags) * CONFIDENCE_WEIGHT
    rule_penalty = _rule_penalty(tags)

    budget_penalty = 0.0
    if not event.is_free and event.price_min is not None:
        budget_limit = profile.constraints.budget_per_event
        if event.price_min > budget_limit:
            budget_penalty = min(10.0, ((event.price_min - budget_limit) / budget_limit) * 10.0)

    final = (
        toddler_fit
        + intrinsic
        + interest
        + weather_pts
        + timing
        + logistics
        + novelty
        + city_pts
        + confidence
        - budget_penalty
        - rule_penalty
    )

    return ScoreBreakdown(
        final=max(0.0, round(final, 2)),
        toddler_fit=round(toddler_fit, 2),
        intrinsic=round(intrinsic, 2),
        interest=round(interest, 2),
        weather=round(weather_pts, 2),
        timing=round(timing, 2),
        logistics=round(logistics, 2),
        novelty=round(novelty, 2),
        city=round(city_pts, 2),
        confidence=round(confidence, 2),
        budget_penalty=round(budget_penalty, 2),
        rule_penalty=round(rule_penalty, 2),
    )


def _interest_score(categories: list[str], profile: InterestProfile) -> float:
    score = 0.0
    for cat in categories:
        interest = _CAT_TO_INTEREST.get(cat, cat)
        if interest in profile.loves:
            score += 10.0
        elif interest in profile.likes:
            score += 5.0
    return _normalize_to_ten(score, max_value=30.0)


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

    if forecast.precipitation_pct > 50:
        if tags.indoor_outdoor == "indoor" or tags.good_for_rain:
            score += 3.0
        elif tags.indoor_outdoor == "outdoor" and tags.weather_dependent:
            score -= 4.0

    if forecast.temp_high_f > 95:
        if tags.indoor_outdoor == "indoor" or tags.good_for_heat:
            score += 2.0
        elif tags.indoor_outdoor == "outdoor":
            if event.start_time.hour < 11:
                score += 0.5
            else:
                score -= 3.0

    if (
        65 < forecast.temp_high_f < 85
        and forecast.precipitation_pct < 30
        and tags.indoor_outdoor in ("outdoor", "both")
    ):
        score += 2.0

    return max(0.0, min(10.0, score))


def _timing_score(event: Event, profile: InterestProfile, tags: EventTags | None = None) -> float:
    score = 5.0
    hour = event.start_time.hour

    nap_start = profile.constraints.nap_start
    nap_end = profile.constraints.nap_end
    bedtime = profile.constraints.bedtime_time

    event_time = event.start_time.time()

    if nap_start <= event_time <= nap_end:
        score -= 4.0
    if event_time >= bedtime:
        score -= 6.0
    if 9 <= hour <= 11:
        score += 3.0
    elif 11 < hour < 13:
        score += 1.5

    if tags is not None and not tags.nap_compatible:
        score -= 1.5

    return max(0.0, min(10.0, score))


def _logistics_score(tags: EventTags) -> float:
    score = 4.0
    if tags.stroller_friendly:
        score += 1.5
    if tags.parking_available:
        score += 1.0
    if tags.bathroom_accessible:
        score += 1.2
    if tags.nap_compatible:
        score += 0.8
    if tags.food_available:
        score += 0.5
    if tags.meltdown_risk == "low":
        score += 1.5
    elif tags.meltdown_risk == "high":
        score -= 2.0
    if tags.parent_attention_required == "minimal":
        score += 0.5
    elif tags.parent_attention_required == "full":
        score -= 1.0
    return max(0.0, min(10.0, score))


def _city_score(event: Event, profile: InterestProfile) -> float:
    """Boost events in the user's home city, penalize far-away ones."""
    home = profile.constraints.home_city
    if event.location_city == home:
        return 10.0
    if event.location_city in profile.constraints.preferred_cities:
        return 6.0
    return 1.0


# ---------------------------------------------------------------------------
# Public ranking function
# ---------------------------------------------------------------------------


def rank_events(
    events: list[Event],
    profile: InterestProfile,
    weather: dict[str, DayForecast],
) -> list[tuple[Event, float]]:
    """Rank events by score, return sorted list of (event, score)."""
    scored = [(e, score_event_breakdown(e, profile, weather).final) for e in events if e.tags]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored
