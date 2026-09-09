"""Validated data models for trips, travelers, and activities."""

from __future__ import annotations

from enum import Enum
from datetime import date, datetime
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


class TimePreference(str, Enum):
    """Optional part of the day a traveler generally prefers."""

    MORNING = "morning"
    EVENING = "evening"
    FLEXIBLE = "flexible"


class TimeBlock(str, Enum):
    """Broad itinerary period without claiming an exact reservation time."""

    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"


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
    daily_budget_level: BudgetLevel | None = None
    food_restrictions: list[str] = Field(default_factory=list, max_length=12)
    must_do_activities: list[str] = Field(default_factory=list, max_length=12)
    pace_preference: TripPace | None = None
    time_preference: TimePreference | None = None
    note: str | None = Field(default=None, min_length=1, max_length=300)

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


class TripBasics(TripSyncModel):
    """Shared trip details that can exist before every traveler has replied."""

    destination: str = Field(min_length=1, max_length=120)
    country: str = Field(min_length=1, max_length=80)
    days: int = Field(ge=1, le=5)
    budget_level: BudgetLevel
    pace: TripPace
    start_date: date | None = None
    end_date: date | None = None
    accommodation_neighborhood: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
    )

    @field_validator("accommodation_neighborhood", mode="before")
    @classmethod
    def normalize_optional_neighborhood(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        return value.strip() or None

    @model_validator(mode="after")
    def validate_trip_dates(self) -> TripBasics:
        if self.start_date is None and self.end_date is None:
            return self
        if self.start_date is None or self.end_date is None:
            raise ValueError("start and end dates must be provided together")
        dated_days = (self.end_date - self.start_date).days + 1
        if dated_days < 1:
            raise ValueError("end date cannot be before start date")
        if dated_days > 5:
            raise ValueError("trip dates cannot span more than five days")
        if dated_days != self.days:
            raise ValueError("trip dates must match the selected number of days")
        return self


class TripRequest(TripBasics):
    """Complete trip inputs used by recommendation and itinerary planning."""

    travelers: list[TravelerProfile] = Field(min_length=2, max_length=6)

    @model_validator(mode="after")
    def require_unique_traveler_names(self) -> TripRequest:
        normalized_names = [traveler.name.casefold() for traveler in self.travelers]
        if len(normalized_names) != len(set(normalized_names)):
            raise ValueError("traveler names must be unique")
        return self

    @property
    def planning_budget_level(self) -> BudgetLevel:
        """Return the group's upper-middle submitted budget, with legacy fallback."""

        ranks = {
            BudgetLevel.FREE: 0,
            BudgetLevel.LOW: 1,
            BudgetLevel.MODERATE: 2,
            BudgetLevel.HIGH: 3,
        }
        submitted = sorted(
            (
                traveler.daily_budget_level
                for traveler in self.travelers
                if traveler.daily_budget_level is not None
            ),
            key=ranks.__getitem__,
        )
        return submitted[len(submitted) // 2] if submitted else self.budget_level

    @property
    def planning_pace(self) -> TripPace:
        """Return the group's upper-middle submitted pace, with legacy fallback."""

        ranks = {
            TripPace.RELAXED: 0,
            TripPace.BALANCED: 1,
            TripPace.PACKED: 2,
        }
        submitted = sorted(
            (
                traveler.pace_preference
                for traveler in self.travelers
                if traveler.pace_preference is not None
            ),
            key=ranks.__getitem__,
        )
        return submitted[len(submitted) // 2] if submitted else self.pace


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
    official_site_verified: bool = False
    official_visit_url: HttpUrl | None = None
    official_hours_url: HttpUrl | None = None
    official_tickets_url: HttpUrl | None = None
    official_site_checked_at: datetime | None = None
    wikipedia_url: HttpUrl | None = None
    wikidata_id: str | None = Field(default=None, pattern=r"^Q\d+$")
    address: str | None = Field(default=None, min_length=1, max_length=300)
    opening_hours: str | None = Field(default=None, min_length=1, max_length=250)
    osm_url: HttpUrl | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    @field_validator(
        "official_url",
        "official_visit_url",
        "official_hours_url",
        "official_tickets_url",
        "wikipedia_url",
        "osm_url",
        mode="before",
    )
    @classmethod
    def normalize_optional_urls(cls, value: Any) -> Any:
        """Accept older catalog links that were stored without a scheme.

        Curated records created before these fields used ``HttpUrl`` sometimes
        contain a plain domain such as ``museum.example.org``.  Treat that as a
        conventional HTTPS link when the record is next validated, rather than
        preventing unrelated catalog enrichment from completing.
        """

        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if normalized and "://" not in normalized:
            return f"https://{normalized}"
        return normalized or None

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
        official_detail_urls = (
            self.official_visit_url,
            self.official_hours_url,
            self.official_tickets_url,
        )
        if self.official_site_verified and self.official_url is None:
            raise ValueError("a verified official site requires official_url")
        if any(official_detail_urls) and not self.official_site_verified:
            raise ValueError(
                "official visitor links require a verified official site"
            )
        return self


class ScheduledActivity(TripSyncModel):
    """A grounded activity placed on one itinerary day."""

    activity_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    activity_name: str = Field(min_length=1, max_length=160)
    duration_hours: float = Field(gt=0, le=12)
    time_block: TimeBlock | None = None
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
        time_blocks = [activity.time_block for activity in self.activities]
        if any(block is not None for block in time_blocks) and any(
            block is None for block in time_blocks
        ):
            raise ValueError("time blocks must cover every activity in a day")
        if time_blocks and all(block is not None for block in time_blocks):
            block_order = {
                TimeBlock.MORNING: 0,
                TimeBlock.AFTERNOON: 1,
                TimeBlock.EVENING: 2,
            }
            order = [
                block_order[block]
                for block in time_blocks
                if block is not None
            ]
            if order != sorted(order):
                raise ValueError("time blocks must follow the day's activity order")

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
