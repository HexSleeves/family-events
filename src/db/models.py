"""Pydantic models for the family-events database layer."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# EventTags - AI-generated metadata about an event
# ---------------------------------------------------------------------------


class EventTags(BaseModel):
    age_min_recommended: int = 0
    age_max_recommended: int = 12
    toddler_score: int = Field(default=5, ge=0, le=10)
    indoor_outdoor: Literal["indoor", "outdoor", "both"] = "both"
    noise_level: Literal["quiet", "moderate", "loud"] = "moderate"
    crowd_level: Literal["small", "medium", "large"] = "medium"
    stroller_friendly: bool = True
    parking_available: bool = True
    bathroom_accessible: bool = True
    food_available: bool = False
    nap_compatible: bool = False
    categories: list[str] = Field(default_factory=list)
    energy_level: Literal["calm", "moderate", "active"] = "moderate"
    weather_dependent: bool = False
    good_for_rain: bool = False
    good_for_heat: bool = False
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    parent_attention_required: Literal["full", "partial", "minimal"] = "full"
    meltdown_risk: Literal["low", "medium", "high"] = "medium"


# ---------------------------------------------------------------------------
# Event - a single scraped event
# ---------------------------------------------------------------------------


class Event(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source: str
    source_url: str
    source_id: str
    title: str
    description: str = ""
    location_name: str = ""
    location_address: str = ""
    location_city: str = "Lafayette"  # Typically "Lafayette", "Baton Rouge", or "Other"
    latitude: float | None = None
    longitude: float | None = None
    start_time: datetime
    end_time: datetime | None = None
    is_recurring: bool = False
    recurrence_rule: str | None = None
    is_free: bool = True
    price_min: float | None = None
    price_max: float | None = None
    image_url: str | None = None
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    raw_data: dict[str, Any] = Field(default_factory=dict)
    tags: EventTags | None = None
    attended: bool = False


# ---------------------------------------------------------------------------
# InterestProfile - child preferences & family constraints
# ---------------------------------------------------------------------------


class Constraints(BaseModel):
    max_drive_time_minutes: int = 45
    preferred_cities: list[str] = Field(default_factory=lambda: ["Lafayette", "Baton Rouge"])
    home_city: str = "Lafayette"  # Primary city gets ranking boost
    nap_time: str = "13:00-15:00"  # HH:MM-HH:MM
    bedtime: str = "19:30"  # HH:MM
    budget_per_event: float = 30.0

    @property
    def nap_start(self) -> time:
        h, m = self.nap_time.split("-")[0].split(":")
        return time(int(h), int(m))

    @property
    def nap_end(self) -> time:
        h, m = self.nap_time.split("-")[1].split(":")
        return time(int(h), int(m))

    @property
    def bedtime_time(self) -> time:
        h, m = self.bedtime.split(":")
        return time(int(h), int(m))


class InterestProfile(BaseModel):
    loves: list[str] = Field(
        default_factory=lambda: [
            "animals",
            "playground",
            "water_play",
            "music",
            "trains",
            "art_messy",
        ]
    )
    likes: list[str] = Field(default_factory=lambda: ["nature_walks", "story_time", "dancing"])
    dislikes: list[str] = Field(
        default_factory=lambda: ["loud_crowds", "sitting_still_long", "dark_spaces"]
    )
    constraints: Constraints = Field(default_factory=Constraints)


# ---------------------------------------------------------------------------
# Source - a user-defined scraping source
# ---------------------------------------------------------------------------


class Source(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    url: str
    domain: str
    builtin: bool = False
    recipe_json: str | None = None  # JSON string of ScrapeRecipe
    enabled: bool = True
    status: Literal["pending", "analyzing", "active", "stale", "failed", "disabled"] = "pending"
    last_scraped_at: datetime | None = None
    last_event_count: int = 0
    last_error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
