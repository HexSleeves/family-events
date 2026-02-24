"""LLM-based event tagger using OpenAI."""

import json

from openai import AsyncOpenAI

from src.config import settings
from src.db.models import Event, EventTags

SYSTEM_PROMPT = """You are an expert at evaluating family events for toddler-friendliness. You have extensive experience with child development and understand the needs of a 3-year-old.

Analyze the following event and provide structured tags. Consider:

FOR A 3-YEAR-OLD:
- Attention span: 10-15 minutes per activity
- Nap schedule: Usually needs afternoon nap (1-3pm is risky)
- Noise sensitivity: Can be overwhelmed by loud environments
- Mobility: Walks but tires easily, may need stroller
- Interests: Animals, music, water play, playground, trains, art/mess-making

LOUISIANA CONTEXT:
- Summer heat is extreme (May-September) — outdoor events need shade/water
- Mosquitoes are a factor for evening outdoor events
- Hurricane season (June-November) may affect outdoor events

Return ONLY a JSON object with these exact fields:
{
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
  "meltdown_risk": "low" | "medium" | "high"
}"""


class EventTagger:
    def __init__(self) -> None:
        self._use_llm = bool(settings.openai_api_key)
        if self._use_llm:
            self.client = AsyncOpenAI(api_key=settings.openai_api_key)
            self.model = settings.openai_model
        else:
            self.client = None
            self.model = "heuristic"

    async def tag_event(self, event: Event) -> EventTags:
        if not self._use_llm:
            return self._heuristic_tag(event)
        return await self._llm_tag(event)

    def _heuristic_tag(self, event: Event) -> EventTags:
        """Rule-based fallback tagger when no LLM API key is configured."""
        title = (event.title + " " + event.description).lower()
        cats: list[str] = []
        toddler_score = 5  # default

        # Category detection
        if any(w in title for w in ["zoo", "animal", "petting", "farm", "wildlife"]):
            cats.append("animals"); toddler_score += 2
        if any(w in title for w in ["art", "craft", "paint", "ceramic", "creative"]):
            cats.append("arts"); toddler_score += 1
        if any(w in title for w in ["music", "concert", "sing", "band", "jazz"]):
            cats.append("music")
        if any(w in title for w in ["nature", "swamp", "trail", "garden", "hike", "park"]):
            cats.append("nature")
        if any(w in title for w in ["story", "book", "read", "library", "learn"]):
            cats.append("learning"); toddler_score += 1
        if any(w in title for w in ["play", "playground", "bounce", "jump", "kid", "toddler", "child", "youth"]):
            cats.append("play"); toddler_score += 2
        if any(w in title for w in ["sport", "soccer", "baseball", "basketball", "fit"]):
            cats.append("sports")
        if any(w in title for w in ["splash", "swim", "pool", "water", "aqua"]):
            cats.append("water"); toddler_score += 2

        # Detect indoor/outdoor
        indoor_outdoor = "both"
        if any(w in title for w in ["indoor", "library", "museum", "studio", "classroom"]):
            indoor_outdoor = "indoor"
        elif any(w in title for w in ["outdoor", "park", "trail", "garden", "splash"]):
            indoor_outdoor = "outdoor"

        # Family/kid oriented boost
        if any(w in title for w in ["family", "kid", "toddler", "preschool", "baby", "child", "youth", "beginnings"]):
            toddler_score += 1

        # Adult-oriented penalty
        if any(w in title for w in ["bar", "wine", "beer", "cocktail", "adults only", "senior", "5k", "marathon", "trivia"]):
            toddler_score = max(1, toddler_score - 4)

        # Time-based nap compatibility
        nap_compat = True
        if event.start_time.hour >= 13 and event.start_time.hour <= 15:
            nap_compat = False

        # Clamp score
        toddler_score = max(1, min(10, toddler_score))
        if not cats:
            cats = ["play"]

        return EventTags(
            age_min_recommended=0 if toddler_score >= 6 else 5,
            age_max_recommended=99,
            toddler_score=toddler_score,
            indoor_outdoor=indoor_outdoor,
            noise_level="moderate",
            crowd_level="medium",
            stroller_friendly=True,
            parking_available=True,
            bathroom_accessible=True,
            food_available=False,
            nap_compatible=nap_compat,
            categories=cats[:3],
            energy_level="moderate",
            weather_dependent=indoor_outdoor == "outdoor",
            good_for_rain=indoor_outdoor == "indoor",
            good_for_heat=indoor_outdoor == "indoor" or "water" in cats,
            confidence_score=0.5,
            parent_attention_required="partial",
            meltdown_risk="medium",
        )

    async def _llm_tag(self, event: Event) -> EventTags:
        """Tag a single event using the LLM."""
        user_prompt = (
            f"Event: {event.title}\n"
            f"Description: {(event.description or 'No description')[:1500]}\n"
            f"Location: {event.location_name}, {event.location_city}\n"
            f"Date/Time: {event.start_time.strftime('%A %B %d, %Y at %I:%M %p')}\n"
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
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        raw = json.loads(response.choices[0].message.content)
        return EventTags.model_validate(raw)

    async def tag_events(
        self, events: list[Event]
    ) -> list[tuple[Event, EventTags]]:
        """Tag multiple events. Returns list of (event, tags) pairs."""
        results: list[tuple[Event, EventTags]] = []
        for event in events:
            try:
                tags = await self.tag_event(event)
                results.append((event, tags))
                print(f"  Tagged: {event.title} \u2192 toddler_score={tags.toddler_score}")
            except Exception as e:
                print(f"  Failed to tag '{event.title}': {e}")
        return results
