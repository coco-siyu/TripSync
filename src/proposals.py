"""Structured, grounded itinerary-adjustment proposals."""

from __future__ import annotations

from typing import Literal, Sequence

from pydantic import Field, model_validator

from src.models import Activity, ItineraryPlan, TripSyncModel


class ItineraryChangeProposal(TripSyncModel):
    """One user-reviewable change to an existing itinerary."""

    title: str = Field(min_length=1, max_length=120)
    operation: Literal["replace", "remove"]
    day_number: int = Field(ge=1, le=5)
    remove_activity_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    add_activity_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$",
    )
    rationale: str = Field(min_length=1, max_length=600)
    tradeoffs: list[str] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def require_a_replacement_only_when_needed(self) -> ItineraryChangeProposal:
        if self.operation == "replace" and self.add_activity_id is None:
            raise ValueError("replace proposals need an add_activity_id")
        if self.operation == "remove" and self.add_activity_id is not None:
            raise ValueError("remove proposals cannot add an activity")
        if self.add_activity_id == self.remove_activity_id:
            raise ValueError("a proposal cannot replace an activity with itself")
        return self


class ItineraryChangeProposals(TripSyncModel):
    """A small set of structured options responding to a user request."""

    acknowledgement: str = Field(min_length=1, max_length=400)
    proposals: list[ItineraryChangeProposal] = Field(
        min_length=1,
        max_length=3,
    )


class ProposalGroundingError(ValueError):
    """Raised when a proposal refers to an unavailable itinerary activity."""


def validate_proposals_against_plan(
    proposals: ItineraryChangeProposals,
    plan: ItineraryPlan,
    activities: Sequence[Activity],
) -> ItineraryChangeProposals:
    """Require proposal IDs to exist in the fixed plan or eligible catalog."""

    scheduled_by_day = {
        day.day_number: {activity.activity_id for activity in day.activities}
        for day in plan.days
    }
    scheduled_ids = {
        activity_id
        for activity_ids in scheduled_by_day.values()
        for activity_id in activity_ids
    }
    eligible_ids = {activity.id for activity in activities}

    for proposal in proposals.proposals:
        if proposal.remove_activity_id not in scheduled_by_day.get(
            proposal.day_number,
            set(),
        ):
            raise ProposalGroundingError(
                f"{proposal.remove_activity_id} is not scheduled on Day "
                f"{proposal.day_number}"
            )
        if proposal.add_activity_id is not None:
            if proposal.add_activity_id not in eligible_ids:
                raise ProposalGroundingError(
                    f"{proposal.add_activity_id} is not an eligible catalog activity"
                )
            if proposal.add_activity_id in scheduled_ids:
                raise ProposalGroundingError(
                    f"{proposal.add_activity_id} is already in the itinerary"
                )
    return proposals
