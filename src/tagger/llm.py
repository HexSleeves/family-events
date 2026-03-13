"""LLM-based event tagger using OpenAI."""

import asyncio
import json
import logging
import time as pytime
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import time
from typing import Literal

from openai import AsyncOpenAI

from src.config import settings
from src.db.models import Event, EventTags, InterestProfile
from src.tagger.taxonomy import (
    CATEGORY_RULES,
    CAUTION_RULES,
    EXCLUSION_RULES,
    POSITIVE_RULES,
    TAGGING_VERSION,
)

SYSTEM_PROMPT_TEMPLATE = """You are an expert at evaluating family events for child-friendliness.

Analyze the following event for this specific child and family:

CHILD PROFILE:
- Age: {age_years} years {age_months} months
- Temperament: {temperament}
- Loves: {loves}
- Likes: {likes}
- Dislikes: {dislikes}
- Favorite event categories: {favorite_categories}
- Avoid event categories: {avoid_categories}
- Sensory notes: {sensory_notes}
- Accessibility needs: {accessibility_needs}
- Extra family notes: {notes_for_recommendations}

FAMILY CONSTRAINTS:
- Home city: {home_city}
- Preferred cities: {preferred_cities}
- Max drive time: {max_drive_time_minutes} minutes
- Nap window: {nap_time}
- Bedtime: {bedtime}
- Budget per event: ${budget_per_event}

SCORING GUIDANCE:
- Be specific to this child, not a generic toddler.
- Favor events aligned with their temperament, interests, sensory profile, and routine.
- Penalize events likely to trigger overwhelm, boredom, schedule conflicts, or mobility issues.
- Consider local weather and logistics from the event details when relevant.
- Be conservative: 8+ only if the fit is truly excellent for this child.

Return ONLY a JSON object with these exact fields:
{{
  "tagging_version": "v2",
  "age_min_recommended": int,
  "age_max_recommended": int,
  "toddler_score": int (0-10, be conservative, 8+ only if truly exceptional),
  "indoor_outdoor": "indoor" | "outdoor" | "both",
  "noise_level": "quiet" | "moderate" | "loud",
  "crowd_level": "small" | "medium" | "large",
  "stroller_friendly": boolean,
  "parking_available": boolean,
  "bathroom_accessible": boolean,
  "food_available": boolean,
  "nap_compatible": boolean,
  "categories": ["animals", "arts", "music", "nature", "learning", "play", "sports", "water"],
  "energy_level": "calm" | "moderate" | "active",
  "weather_dependent": boolean,
  "good_for_rain": boolean,
  "good_for_heat": boolean,
  "confidence_score": float (0-1, your confidence in this assessment),
  "parent_attention_required": "full" | "partial" | "minimal",
  "meltdown_risk": "low" | "medium" | "high",
  "audience": "toddler_focused" | "family_mixed" | "general_public" | "adult_skewed",
  "positive_signals": [string],
  "caution_signals": [string],
  "exclusion_signals": [string],
  "raw_rule_score": int (0-100, estimate the event's intrinsic child fit before personalization)
}}"""

INDOOR_TERMS = ("indoor", "library", "museum", "studio", "classroom", "gym")
OUTDOOR_TERMS = ("outdoor", "park", "trail", "garden", "splash", "farm")
LOUD_TERMS = ("concert", "pep rally", "dj", "loud", "festival")
QUIET_TERMS = ("story", "library", "sensory", "museum", "read")
LARGE_CROWD_TERMS = ("festival", "fair", "parade", "concert", "expo")
SMALL_CROWD_TERMS = ("story time", "storytime", "class", "playgroup", "sensory")
FOOD_TERMS = ("food", "snack", "lunch", "breakfast", "picnic", "vendors")
PARKING_TERMS = ("parking", "lot", "garage")
STAIRS_TERMS = ("stairs", "upstairs", "historic")
BATHROOM_TERMS = ("restroom", "bathroom", "facility", "visitor center", "library")
WATER_TERMS = CATEGORY_RULES["water"]
logger = logging.getLogger("uvicorn.error")


def _duration_ms(started: float) -> float:
    return round((pytime.perf_counter() - started) * 1000, 2)


def _runtime_log(level: int, event: str, **context: object) -> None:
    logger.log(
        level,
        event,
        extra={key: value for key, value in context.items() if value is not None},
    )


def _error_details(exc: BaseException) -> tuple[str, str]:
    message = str(exc).strip() or repr(exc)
    return type(exc).__name__, message


@dataclass(slots=True)
class RuleEvaluation:
    age_max_recommended: int
    age_min_recommended: int
    audience: Literal["toddler_focused", "family_mixed", "general_public", "adult_skewed"]
    bathroom_accessible: bool
    categories: list[str]
    caution_signals: list[str]
    confidence_score: float
    crowd_level: Literal["small", "medium", "large"]
    energy_level: Literal["calm", "moderate", "active"]
    exclusion_signals: list[str]
    food_available: bool
    good_for_heat: bool
    good_for_rain: bool
    indoor_outdoor: Literal["indoor", "outdoor", "both"]
    meltdown_risk: Literal["low", "medium", "high"]
    nap_compatible: bool
    noise_level: Literal["quiet", "moderate", "loud"]
    parent_attention_required: Literal["full", "partial", "minimal"]
    parking_available: bool
    positive_signals: list[str]
    raw_score: int
    stroller_friendly: bool
    weather_dependent: bool


class EventTagger:
    def __init__(self, profile: InterestProfile | None = None) -> None:
        self.profile = profile or InterestProfile()
        self._use_llm = bool(settings.openai_api_key)
        self._concurrency = max(1, settings.tagger_concurrency)
        if self._use_llm:
            self.client = AsyncOpenAI(
                api_key=settings.openai_api_key,
                timeout=settings.openai_timeout_seconds,
                max_retries=settings.openai_max_retries,
            )
            self.model = settings.openai_model
        else:
            self.client = None
            self.model = "heuristic"

    async def tag_event(self, event: Event) -> EventTags:
        if not self._use_llm:
            return self._heuristic_tag(event)
        return await self._llm_tag(event)

    def _system_prompt(self) -> str:
        profile = self.profile
        constraints = profile.constraints
        return SYSTEM_PROMPT_TEMPLATE.format(
            age_years=profile.child_age_years,
            age_months=profile.child_age_months,
            temperament=profile.temperament or "not provided",
            loves=", ".join(profile.loves) or "not provided",
            likes=", ".join(profile.likes) or "not provided",
            dislikes=", ".join(profile.dislikes) or "not provided",
            favorite_categories=", ".join(profile.favorite_categories) or "none specified",
            avoid_categories=", ".join(profile.avoid_categories) or "none specified",
            sensory_notes=profile.sensory_notes or "none provided",
            accessibility_needs=profile.accessibility_needs or "none provided",
            notes_for_recommendations=profile.notes_for_recommendations or "none provided",
            home_city=constraints.home_city or "not provided",
            preferred_cities=", ".join(constraints.preferred_cities) or "none specified",
            max_drive_time_minutes=constraints.max_drive_time_minutes,
            nap_time=constraints.nap_time,
            bedtime=constraints.bedtime,
            budget_per_event=constraints.budget_per_event,
        )

    def _event_text(self, event: Event) -> str:
        return " ".join(
            part
            for part in [
                event.title,
                event.description,
                event.location_name,
                event.location_address,
                event.location_city,
            ]
            if part
        ).lower()

    def _contains_any(self, haystack: str, needles: tuple[str, ...]) -> bool:
        return any(needle in haystack for needle in needles)

    def _derive_categories(self, text: str) -> list[str]:
        categories: list[str] = []
        for category, terms in CATEGORY_RULES.items():
            if self._contains_any(text, terms):
                categories.append(category)
        if not categories:
            categories.append("play")
        return categories[:4]

    def _rule_based_assessment(self, event: Event) -> RuleEvaluation:
        text = self._event_text(event)
        categories = self._derive_categories(text)

        score = 50
        positive_signals: list[str] = []
        caution_signals: list[str] = []
        exclusion_signals: list[str] = []

        for term, delta, reason in POSITIVE_RULES:
            if term in text:
                score += delta
                positive_signals.append(reason)

        for term, delta, reason in CAUTION_RULES:
            if term in text:
                score += delta
                caution_signals.append(reason)

        for term, delta, reason in EXCLUSION_RULES:
            if term in text:
                score += delta
                exclusion_signals.append(reason)

        if "water" in categories:
            score += 8
            positive_signals.append("water play helps with Louisiana heat")
        if "animals" in categories:
            score += 8
            positive_signals.append("animal encounters are toddler-friendly")
        if "play" in categories:
            score += 10
            positive_signals.append("open-ended play is toddler-friendly")
        if event.is_free:
            score += 4
        if event.start_time.time() >= time(19, 0):
            score -= 18
            caution_signals.append("late start time")
        elif 13 <= event.start_time.hour <= 15:
            score -= 10
            caution_signals.append("starts during nap window")
        elif 9 <= event.start_time.hour <= 11:
            score += 10
            positive_signals.append("morning timing fits toddler routines")

        indoor = self._contains_any(text, INDOOR_TERMS)
        outdoor = self._contains_any(text, OUTDOOR_TERMS)
        if indoor and outdoor:
            indoor_outdoor: Literal["indoor", "outdoor", "both"] = "both"
        elif indoor:
            indoor_outdoor = "indoor"
        elif outdoor:
            indoor_outdoor = "outdoor"
        else:
            indoor_outdoor = "both"

        if self._contains_any(text, LOUD_TERMS):
            noise_level: Literal["quiet", "moderate", "loud"] = "loud"
        elif self._contains_any(text, QUIET_TERMS):
            noise_level = "quiet"
        else:
            noise_level = "moderate"

        if self._contains_any(text, LARGE_CROWD_TERMS):
            crowd_level: Literal["small", "medium", "large"] = "large"
        elif self._contains_any(text, SMALL_CROWD_TERMS):
            crowd_level = "small"
        else:
            crowd_level = "medium"

        stroller_friendly = not self._contains_any(text, STAIRS_TERMS)
        parking_available = self._contains_any(text, PARKING_TERMS) or event.location_city in {
            "Lafayette",
            "Baton Rouge",
        }
        bathroom_accessible = (
            self._contains_any(text, BATHROOM_TERMS) or indoor_outdoor != "outdoor"
        )
        food_available = self._contains_any(text, FOOD_TERMS)
        nap_compatible = not (13 <= event.start_time.hour <= 15)
        weather_dependent = indoor_outdoor == "outdoor"
        good_for_rain = indoor_outdoor in {"indoor", "both"}
        good_for_heat = indoor_outdoor == "indoor" or self._contains_any(text, WATER_TERMS)

        energy_level: Literal["calm", "moderate", "active"]
        if "sports" in categories or "water" in categories or "play" in categories:
            energy_level = "active"
        elif "learning" in categories and noise_level == "quiet":
            energy_level = "calm"
        else:
            energy_level = "moderate"

        if exclusion_signals:
            audience: Literal["toddler_focused", "family_mixed", "general_public", "adult_skewed"]
            audience = "adult_skewed"
        elif any(term in text for term in ("toddler", "preschool", "story time", "playgroup")):
            audience = "toddler_focused"
        elif any(term in text for term in ("family", "kids", "children")):
            audience = "family_mixed"
        else:
            audience = "general_public"

        parent_attention_required: Literal["full", "partial", "minimal"] = "partial"
        if crowd_level == "large" or noise_level == "loud":
            parent_attention_required = "full"
        elif audience == "toddler_focused" and noise_level == "quiet":
            parent_attention_required = "minimal"

        risk_points = 0
        if noise_level == "loud":
            risk_points += 2
        if crowd_level == "large":
            risk_points += 2
        if not nap_compatible:
            risk_points += 1
        if event.start_time.time() >= time(19, 0):
            risk_points += 2
        if audience == "adult_skewed":
            risk_points += 3

        meltdown_risk: Literal["low", "medium", "high"]
        if risk_points >= 5:
            meltdown_risk = "high"
        elif risk_points >= 2:
            meltdown_risk = "medium"
        else:
            meltdown_risk = "low"

        if audience == "adult_skewed":
            age_min = 8
        elif audience == "toddler_focused":
            age_min = 1
        else:
            age_min = 3
        age_max = 12 if audience != "adult_skewed" else 99

        confidence = 0.55
        if len(positive_signals) + len(caution_signals) + len(exclusion_signals) >= 4:
            confidence = 0.72
        if event.description:
            confidence += 0.08
        confidence = min(confidence, 0.9)

        score = max(0, min(100, score))
        return RuleEvaluation(
            raw_score=score,
            categories=categories,
            audience=audience,
            positive_signals=positive_signals[:5],
            caution_signals=caution_signals[:5],
            exclusion_signals=exclusion_signals[:5],
            indoor_outdoor=indoor_outdoor,
            noise_level=noise_level,
            crowd_level=crowd_level,
            stroller_friendly=stroller_friendly,
            parking_available=parking_available,
            bathroom_accessible=bathroom_accessible,
            food_available=food_available,
            nap_compatible=nap_compatible,
            energy_level=energy_level,
            weather_dependent=weather_dependent,
            good_for_rain=good_for_rain,
            good_for_heat=good_for_heat,
            parent_attention_required=parent_attention_required,
            meltdown_risk=meltdown_risk,
            age_min_recommended=age_min,
            age_max_recommended=age_max,
            confidence_score=confidence,
        )

    def _heuristic_tag(self, event: Event) -> EventTags:
        """Rule-based fallback tagger when no LLM API key is configured."""
        rule_eval = self._rule_based_assessment(event)
        toddler_score = round(rule_eval.raw_score / 10)
        toddler_score = max(0, min(10, toddler_score))

        return EventTags(
            tagging_version=TAGGING_VERSION,
            age_min_recommended=rule_eval.age_min_recommended,
            age_max_recommended=rule_eval.age_max_recommended,
            toddler_score=toddler_score,
            indoor_outdoor=rule_eval.indoor_outdoor,
            noise_level=rule_eval.noise_level,
            crowd_level=rule_eval.crowd_level,
            stroller_friendly=rule_eval.stroller_friendly,
            parking_available=rule_eval.parking_available,
            bathroom_accessible=rule_eval.bathroom_accessible,
            food_available=rule_eval.food_available,
            nap_compatible=rule_eval.nap_compatible,
            categories=rule_eval.categories,
            energy_level=rule_eval.energy_level,
            weather_dependent=rule_eval.weather_dependent,
            good_for_rain=rule_eval.good_for_rain,
            good_for_heat=rule_eval.good_for_heat,
            confidence_score=rule_eval.confidence_score,
            parent_attention_required=rule_eval.parent_attention_required,
            meltdown_risk=rule_eval.meltdown_risk,
            audience=rule_eval.audience,
            positive_signals=rule_eval.positive_signals,
            caution_signals=rule_eval.caution_signals,
            exclusion_signals=rule_eval.exclusion_signals,
            raw_rule_score=rule_eval.raw_score,
        )

    async def _llm_tag(self, event: Event) -> EventTags:
        """Tag a single event using the LLM."""
        rule_eval = self._rule_based_assessment(event)
        user_prompt = (
            f"Event: {event.title}\n"
            f"Description: {(event.description or 'No description')[:1500]}\n"
            f"Location: {event.location_name}, {event.location_city}\n"
            f"Date/Time: {event.start_time.strftime('%A %B %d, %Y at %I:%M %p')}\n"
            f"Rule baseline score: {rule_eval.raw_score}/100\n"
            f"Rule audience: {rule_eval.audience}\n"
            f"Rule categories: {', '.join(rule_eval.categories)}\n"
            f"Positive signals: {', '.join(rule_eval.positive_signals) or 'none'}\n"
            f"Caution signals: {', '.join(rule_eval.caution_signals) or 'none'}\n"
            f"Exclusion signals: {', '.join(rule_eval.exclusion_signals) or 'none'}\n"
            "Use the rule baseline as a starting point, but correct it if the event details justify it.\n"
        )
        if event.end_time:
            user_prompt += f"Ends: {event.end_time.strftime('%I:%M %p')}\n"
        if event.is_free:
            user_prompt += "Price: Free\n"
        else:
            user_prompt += f"Price: ${event.price_min or '?'} - ${event.price_max or '?'}\n"

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )

        raw = json.loads(response.choices[0].message.content or "{}")
        raw.setdefault("tagging_version", TAGGING_VERSION)
        raw.setdefault("raw_rule_score", rule_eval.raw_score)
        raw.setdefault("positive_signals", rule_eval.positive_signals)
        raw.setdefault("caution_signals", rule_eval.caution_signals)
        raw.setdefault("exclusion_signals", rule_eval.exclusion_signals)
        raw.setdefault("audience", rule_eval.audience)
        return EventTags.model_validate(raw)

    async def _tag_event_safe(
        self, semaphore: asyncio.Semaphore, event: Event
    ) -> tuple[Event, EventTags] | None:
        async with semaphore:
            started = pytime.perf_counter()
            try:
                tags = await self.tag_event(event)
                _runtime_log(
                    logging.INFO,
                    "tag_event_succeeded",
                    stage="tag",
                    event_id=event.id,
                    event_title=event.title,
                    source=event.source,
                    source_id=event.source_id,
                    source_url=event.source_url,
                    toddler_score=tags.toddler_score,
                    raw_rule_score=tags.raw_rule_score,
                    audience=tags.audience,
                    duration_ms=_duration_ms(started),
                )
                return event, tags
            except Exception as exc:
                error_type, error_message = _error_details(exc)
                _runtime_log(
                    logging.ERROR,
                    "tag_event_failed",
                    stage="tag",
                    event_id=event.id,
                    event_title=event.title,
                    source=event.source,
                    source_id=event.source_id,
                    source_url=event.source_url,
                    error_type=error_type,
                    error_message=error_message,
                    duration_ms=_duration_ms(started),
                )
                return None

    async def tag_events(self, events: list[Event]) -> list[tuple[Event, EventTags]]:
        """Tag multiple events. Returns list of (event, tags) pairs."""
        semaphore = asyncio.Semaphore(self._concurrency)
        tasks = [self._tag_event_safe(semaphore, event) for event in events]
        tagged = await asyncio.gather(*tasks)
        return [result for result in tagged if result is not None]

    async def tag_events_in_batches(
        self,
        events: list[Event],
        *,
        batch_size: int,
        on_batch_complete: Callable[
            [int, list[Event], list[tuple[Event, EventTags]], list[tuple[Event, EventTags]]],
            Awaitable[None],
        ]
        | None = None,
    ) -> list[tuple[Event, EventTags]]:
        """Tag events in batches, optionally reporting progress after each batch."""
        if batch_size <= 0:
            raise ValueError("batch_size must be > 0")

        all_results: list[tuple[Event, EventTags]] = []
        for start_idx in range(0, len(events), batch_size):
            batch = events[start_idx : start_idx + batch_size]
            started = pytime.perf_counter()
            _runtime_log(
                logging.INFO,
                "tag_batch_started",
                stage="tag",
                batch_start=start_idx,
                batch_size=len(batch),
                total_events=len(events),
            )
            tagged_batch = await self.tag_events(batch)
            all_results.extend(tagged_batch)
            _runtime_log(
                logging.INFO,
                "tag_batch_succeeded",
                stage="tag",
                batch_start=start_idx,
                batch_size=len(batch),
                tagged_count=len(tagged_batch),
                failed_count=len(batch) - len(tagged_batch),
                duration_ms=_duration_ms(started),
            )
            if on_batch_complete is not None:
                await on_batch_complete(start_idx, batch, tagged_batch, all_results)
        return all_results
