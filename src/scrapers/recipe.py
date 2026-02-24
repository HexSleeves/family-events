"""Pydantic models for scrape recipes."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class FieldRule(BaseModel):
    """Rule for extracting a single field from an event container."""

    selector: str | None = None
    attr: str | None = None  # None = textContent, "href", "datetime", "src", etc.
    format: str = "human"  # "iso", "human", or a strptime pattern
    default: str = ""


class Pagination(BaseModel):
    next_selector: str | None = None
    max_pages: int = 3


class CSSFields(BaseModel):
    title: FieldRule
    description: FieldRule | None = None
    start_time: FieldRule | None = None
    end_time: FieldRule | None = None
    location: FieldRule | None = None
    url: FieldRule | None = None
    price: FieldRule | None = None
    image: FieldRule | None = None


class CSSStrategy(BaseModel):
    event_container: str
    fields: CSSFields
    pagination: Pagination = Field(default_factory=Pagination)


class JSONLDStrategy(BaseModel):
    event_type: str = "Event"


class ScrapeRecipe(BaseModel):
    version: int = 1
    strategy: Literal["css", "jsonld"]
    analyzed_at: datetime
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: str = ""
    css: CSSStrategy | None = None
    jsonld: JSONLDStrategy | None = None
