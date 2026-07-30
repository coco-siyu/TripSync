"""Validated structured output for grounded itinerary narration."""

from __future__ import annotations

from pydantic import Field, model_validator

from src.models import ItineraryPlan, TripSyncModel


class NarratedActivity(TripSyncModel):
    """LLM-written explanation for one already-scheduled activity."""

    activity_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    why_it_fits: str = Field(min_length=1, max_length=500)
    practical_note: str | None = Field(default=None, max_length=300)


class NarratedDay(TripSyncModel):
    """Narrative for one immutable itinerary day."""

    day_number: int = Field(ge=1, le=5)
    summary: str = Field(min_length=1, max_length=700)
    activities: list[NarratedActivity] = Field(
        default_factory=list,
        max_length=6,
    )
    tradeoffs: list[str] = Field(default_factory=list, max_length=6)


class ItineraryNarrative(TripSyncModel):
    """Structured LLM narrative grounded in an existing itinerary."""

    trip_summary: str = Field(min_length=1, max_length=1200)
    days: list[NarratedDay] = Field(min_length=1, max_length=5)
    overall_tradeoffs: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def require_unique_days_and_activities(self) -> ItineraryNarrative:
        day_numbers = [day.day_number for day in self.days]
        if len(day_numbers) != len(set(day_numbers)):
            raise ValueError("narrative day numbers must be unique")

        activity_ids = [
            activity.activity_id
            for day in self.days
            for activity in day.activities
        ]
        if len(activity_ids) != len(set(activity_ids)):
            raise ValueError(
                "an activity may only appear once in the narrative"
            )
        return self


class NarrationGroundingError(ValueError):
    """Raised when LLM output changes or invents itinerary structure."""


def validate_narrative_against_plan(
    narrative: ItineraryNarrative,
    plan: ItineraryPlan,
) -> ItineraryNarrative:
    """Require narrative days and activity IDs to match the plan exactly."""

    expected_days = [day.day_number for day in plan.days]
    actual_days = [day.day_number for day in narrative.days]
    if actual_days != expected_days:
        raise NarrationGroundingError(
            "narrative days must match the itinerary in the same order"
        )

    for plan_day, narrative_day in zip(
        plan.days,
        narrative.days,
        strict=True,
    ):
        expected_ids = [
            activity.activity_id for activity in plan_day.activities
        ]
        actual_ids = [
            activity.activity_id
            for activity in narrative_day.activities
        ]
        if actual_ids != expected_ids:
            raise NarrationGroundingError(
                f"narrative activities for Day {plan_day.day_number} "
                "must match the itinerary in the same order"
            )

    return narrative
