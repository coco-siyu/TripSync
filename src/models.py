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


class ItinerarySource(str, Enum):
    """Why an activity was added to a generated itinerary."""

    SHORTLIST = "shortlist"
    RECOMMENDATION = "recommendation"


class RejectionReason(str, Enum):
    """Organizer feedback recorded when replacing an activity."""

    TOO_EXPENSIVE = "Too expensive"
    TOO_MUCH_WALKING = "Too much walking"
    NOT_INTERESTING = "Not interesting"
    TIMING_CONCERN = "Timing concern"
    OTHER = "Other"


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
    category_tags: list[str] = Field(default_factory=list, max_length=6)
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
    official_url: HttpUrl | None = None
    wikipedia_url: HttpUrl | None = None
    wikidata_id: str | None = Field(default=None, pattern=r"^Q\d+$")
    address: str | None = Field(default=None, min_length=1, max_length=300)
    opening_hours: str | None = Field(default=None, min_length=1, max_length=250)
    osm_url: HttpUrl | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    @field_validator("category", mode="before")
    @classmethod
    def normalize_category(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("interests", mode="before")
    @classmethod
    def normalize_interests(cls, value: Any) -> Any:
        return _normalize_tags(value)

    @field_validator("category_tags", mode="before")
    @classmethod
    def normalize_category_tags(cls, value: Any) -> Any:
        return _normalize_tags(value)

    @field_validator("interests")
    @classmethod
    def reject_blank_interests(cls, value: list[str]) -> list[str]:
        if any(not interest for interest in value):
            raise ValueError("interests must not be blank")
        return value

    @model_validator(mode="after")
    def require_complete_location(self) -> Activity:
        """Keep route estimates trustworthy when location data is present."""

        if (self.latitude is None) != (self.longitude is None):
            raise ValueError(
                "latitude and longitude must be provided together"
            )
        return self


class ScheduledActivity(TripSyncModel):
    """A grounded activity placed on one itinerary day."""

    activity_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    activity_name: str = Field(min_length=1, max_length=160)
    duration_hours: float = Field(gt=0, le=12)
    source: ItinerarySource
    must_do_owners: list[str] = Field(default_factory=list, max_length=6)
    traveler_names: list[str] = Field(default_factory=list, max_length=6)
    reason: str = Field(min_length=1, max_length=300)


class ItineraryDay(TripSyncModel):
    """One day with validated estimates and an explicit pace override flag."""

    day_number: int = Field(ge=1, le=5)
    activities: list[ScheduledActivity] = Field(default_factory=list, max_length=6)
    activity_hours: float = Field(ge=0, le=24)
    transition_hours: float = Field(ge=0, le=12)
    planned_hours: float = Field(ge=0, le=24)
    capacity_hours: float = Field(gt=0, le=12)
    pace_override_approved: bool = False

    @model_validator(mode="after")
    def validate_daily_totals(self) -> ItineraryDay:
        activity_total = round(
            sum(activity.duration_hours for activity in self.activities),
            2,
        )
        if abs(activity_total - self.activity_hours) > 0.01:
            raise ValueError("activity hours must equal scheduled durations")
        if abs(
            self.activity_hours
            + self.transition_hours
            - self.planned_hours
        ) > 0.01:
            raise ValueError(
                "planned hours must include activities and transitions"
            )
        if (
            self.planned_hours > self.capacity_hours + 0.01
            and not self.pace_override_approved
        ):
            raise ValueError("planned hours exceed daily capacity")
        return self


class UnscheduledActivity(TripSyncModel):
    """A shortlisted activity that could not fit the itinerary."""

    activity_id: str = Field(min_length=1, max_length=200)
    activity_name: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=300)


class RejectedActivity(TripSyncModel):
    """An activity removed by the organizer and retained for reconsideration."""

    activity_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    activity_name: str = Field(min_length=1, max_length=160)
    reason: RejectionReason
    note: str | None = Field(default=None, max_length=300)
    day_number: int = Field(ge=1, le=5)


class ItineraryPlan(TripSyncModel):
    """A deterministic, catalog-grounded multi-day itinerary."""

    destination: str = Field(min_length=1, max_length=120)
    country: str = Field(min_length=1, max_length=80)
    pace: TripPace
    auto_fill: bool
    days: list[ItineraryDay] = Field(min_length=1, max_length=5)
    unscheduled: list[UnscheduledActivity] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_day_sequence_and_unique_activities(self) -> ItineraryPlan:
        expected_days = list(range(1, len(self.days) + 1))
        actual_days = [day.day_number for day in self.days]
        if actual_days != expected_days:
            raise ValueError("itinerary days must be sequential")

        activity_ids = [
            activity.activity_id
            for day in self.days
            for activity in day.activities
        ]
        if len(activity_ids) != len(set(activity_ids)):
            raise ValueError("an activity may only be scheduled once")
        return self
