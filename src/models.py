"""Validated data models for trips, travelers, and activities."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)


class WalkingLevel(str, Enum):
    """Amount of walking an activity requires or a traveler accepts."""

    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


class BudgetLevel(str, Enum):
    """Relative activity or trip budget."""

    FREE = "free"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


class TripPace(str, Enum):
    """Preferred number and intensity of activities in a day."""

    RELAXED = "relaxed"
    BALANCED = "balanced"
    PACKED = "packed"


class TripSyncModel(BaseModel):
    """Shared validation behavior for public TripSync models."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


def _normalize_tags(value: Any) -> Any:
    """Normalize user-entered tag lists while preserving validation errors."""

    if not isinstance(value, list):
        return value

    normalized: list[Any] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            normalized.append(item)
            continue

        tag = item.strip().lower()
        if tag and tag not in seen:
            normalized.append(tag)
            seen.add(tag)

    return normalized


class TravelerProfile(TripSyncModel):
    """Preferences and constraints for one traveler."""

    name: str = Field(min_length=1, max_length=80)
    interests: list[str] = Field(min_length=1, max_length=12)
    walking_tolerance: WalkingLevel
    food_restrictions: list[str] = Field(default_factory=list, max_length=12)
    must_do_activities: list[str] = Field(default_factory=list, max_length=12)

    @field_validator(
        "interests",
        "food_restrictions",
        "must_do_activities",
        mode="before",
    )
    @classmethod
    def normalize_tags(cls, value: Any) -> Any:
        return _normalize_tags(value)

    @field_validator("interests", "food_restrictions", "must_do_activities")
    @classmethod
    def reject_blank_tags(cls, value: list[str]) -> list[str]:
        if any(not item for item in value):
            raise ValueError("tags must not be blank")
        return value


class TripRequest(TripSyncModel):
    """Trip-level inputs used by recommendation and itinerary planning."""

    destination: str = Field(min_length=1, max_length=120)
    country: str = Field(min_length=1, max_length=80)
    days: int = Field(ge=1, le=5)
    budget_level: BudgetLevel
    pace: TripPace
    travelers: list[TravelerProfile] = Field(min_length=2, max_length=6)

    @model_validator(mode="after")
    def require_unique_traveler_names(self) -> TripRequest:
        normalized_names = [traveler.name.casefold() for traveler in self.travelers]
        if len(normalized_names) != len(set(normalized_names)):
            raise ValueError("traveler names must be unique")
        return self


class Activity(TripSyncModel):
    """A grounded activity candidate that can be retrieved and scored."""

    id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    name: str = Field(min_length=1, max_length=160)
    city: str = Field(min_length=1, max_length=120)
    country: str = Field(min_length=1, max_length=80)
    category: str = Field(min_length=1, max_length=60)
    interests: list[str] = Field(min_length=1, max_length=12)
    walking_level: WalkingLevel
    budget_level: BudgetLevel
    duration_hours: float = Field(gt=0, le=12)
    indoor: bool
    family_friendly: bool
    accessibility_notes: str = Field(min_length=1, max_length=500)
    reservation_required: bool
    description: str = Field(min_length=1, max_length=800)
    source_url: HttpUrl

    @field_validator("category", mode="before")
    @classmethod
    def normalize_category(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("interests", mode="before")
    @classmethod
    def normalize_interests(cls, value: Any) -> Any:
        return _normalize_tags(value)

    @field_validator("interests")
    @classmethod
    def reject_blank_interests(cls, value: list[str]) -> list[str]:
        if any(not interest for interest in value):
            raise ValueError("interests must not be blank")
        return value
