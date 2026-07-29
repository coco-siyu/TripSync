"""Deterministic, explainable group-fit scoring."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import Field

from src.models import (
    Activity,
    BudgetLevel,
    TravelerProfile,
    TripRequest,
    TripSyncModel,
    WalkingLevel,
)


INTEREST_WEIGHT = 55.0
WALKING_WEIGHT = 25.0
MUST_DO_WEIGHT = 20.0

GROUP_AVERAGE_WEIGHT = 0.65
FAIRNESS_WEIGHT = 0.20
BUDGET_WEIGHT = 0.15

_WALKING_RANK = {
    WalkingLevel.LOW: 0,
    WalkingLevel.MODERATE: 1,
    WalkingLevel.HIGH: 2,
}

_BUDGET_RANK = {
    BudgetLevel.FREE: 0,
    BudgetLevel.LOW: 1,
    BudgetLevel.MODERATE: 2,
    BudgetLevel.HIGH: 3,
}


class TravelerFit(TripSyncModel):
    """How well one activity serves one traveler."""

    traveler_name: str
    score: float = Field(ge=0, le=100)
    interest_score: float = Field(ge=0, le=INTEREST_WEIGHT)
    walking_score: float = Field(ge=0, le=WALKING_WEIGHT)
    must_do_score: float = Field(ge=0, le=MUST_DO_WEIGHT)
    matched_interests: list[str]
    walking_compatible: bool
    must_do_match: bool
    explanations: list[str]


class GroupFitResult(TripSyncModel):
    """Group-level score and the evidence used to calculate it."""

    activity_id: str
    activity_name: str
    total_score: float = Field(ge=0, le=100)
    average_traveler_score: float = Field(ge=0, le=100)
    fairness_score: float = Field(ge=0, le=100)
    budget_score: float = Field(ge=0, le=100)
    budget_compatible: bool
    coverage_count: int = Field(ge=0)
    coverage_ratio: float = Field(ge=0, le=1)
    traveler_fits: list[TravelerFit]
    tradeoffs: list[str]


def _interest_score(matched_interests: Sequence[str]) -> float:
    """Reward a clear match while capping the benefit of many shared tags."""

    if not matched_interests:
        return 0.0
    if len(matched_interests) == 1:
        return 40.0
    return INTEREST_WEIGHT


def _must_do_match(activity: Activity, traveler: TravelerProfile) -> bool:
    """Match must-do entries against stable activity names and identifiers."""

    activity_keys = {
        activity.id.casefold(),
        activity.id.replace("_", " ").casefold(),
        activity.name.casefold(),
    }
    return any(
        must_do.casefold() in activity_keys
        for must_do in traveler.must_do_activities
    )


def _score_traveler(
    activity: Activity,
    traveler: TravelerProfile,
) -> TravelerFit:
    matched_interests = sorted(set(activity.interests) & set(traveler.interests))
    interest_score = _interest_score(matched_interests)

    walking_compatible = (
        _WALKING_RANK[activity.walking_level]
        <= _WALKING_RANK[traveler.walking_tolerance]
    )
    walking_score = WALKING_WEIGHT if walking_compatible else 0.0

    must_do_match = _must_do_match(activity, traveler)
    must_do_score = MUST_DO_WEIGHT if must_do_match else 0.0

    explanations: list[str] = []
    if matched_interests:
        explanations.append(
            f"Matches interests: {', '.join(matched_interests)}."
        )
    else:
        explanations.append("No direct interest match.")

    if walking_compatible:
        explanations.append(
            f"Walking level {activity.walking_level.value} is within "
            f"{traveler.walking_tolerance.value} tolerance."
        )
    else:
        explanations.append(
            f"Walking conflict: {activity.walking_level.value} exceeds "
            f"{traveler.walking_tolerance.value} tolerance."
        )

    if must_do_match:
        explanations.append("Matches a must-do activity.")

    return TravelerFit(
        traveler_name=traveler.name,
        score=round(interest_score + walking_score + must_do_score, 1),
        interest_score=interest_score,
        walking_score=walking_score,
        must_do_score=must_do_score,
        matched_interests=matched_interests,
        walking_compatible=walking_compatible,
        must_do_match=must_do_match,
        explanations=explanations,
    )


def _score_budget(
    activity: Activity,
    trip: TripRequest,
) -> tuple[float, bool, str | None]:
    difference = _BUDGET_RANK[activity.budget_level] - _BUDGET_RANK[trip.budget_level]
    if difference <= 0:
        return 100.0, True, None
    if difference == 1:
        return (
            50.0,
            False,
            f"Budget trade-off: activity is {activity.budget_level.value}, "
            f"above the group's {trip.budget_level.value} budget.",
        )
    return (
        0.0,
        False,
        f"Budget conflict: activity is {activity.budget_level.value}, "
        f"well above the group's {trip.budget_level.value} budget.",
    )


def score_activity(activity: Activity, trip: TripRequest) -> GroupFitResult:
    """Score one destination-compatible activity for the complete group."""

    if activity.city.casefold() != trip.destination.casefold():
        raise ValueError(
            f"activity city {activity.city!r} does not match "
            f"trip destination {trip.destination!r}"
        )
    if activity.country.casefold() != trip.country.casefold():
        raise ValueError(
            f"activity country {activity.country!r} does not match "
            f"trip country {trip.country!r}"
        )

    traveler_fits = [
        _score_traveler(activity, traveler) for traveler in trip.travelers
    ]
    traveler_scores = [fit.score for fit in traveler_fits]

    average_traveler_score = sum(traveler_scores) / len(traveler_scores)
    fairness_score = min(traveler_scores)
    budget_score, budget_compatible, budget_tradeoff = _score_budget(activity, trip)

    total_score = (
        average_traveler_score * GROUP_AVERAGE_WEIGHT
        + fairness_score * FAIRNESS_WEIGHT
        + budget_score * BUDGET_WEIGHT
    )

    coverage_count = sum(
        bool(fit.matched_interests) or fit.must_do_match for fit in traveler_fits
    )
    tradeoffs = [
        (
            f"Walking conflict for {fit.traveler_name}: "
            f"{activity.walking_level.value} activity."
        )
        for fit in traveler_fits
        if not fit.walking_compatible
    ]
    if budget_tradeoff:
        tradeoffs.append(budget_tradeoff)

    return GroupFitResult(
        activity_id=activity.id,
        activity_name=activity.name,
        total_score=round(total_score, 1),
        average_traveler_score=round(average_traveler_score, 1),
        fairness_score=round(fairness_score, 1),
        budget_score=budget_score,
        budget_compatible=budget_compatible,
        coverage_count=coverage_count,
        coverage_ratio=round(coverage_count / len(traveler_fits), 3),
        traveler_fits=traveler_fits,
        tradeoffs=tradeoffs,
    )


def rank_activities(
    activities: Sequence[Activity],
    trip: TripRequest,
) -> list[GroupFitResult]:
    """Score and rank activities with stable tie-breaking."""

    results = [score_activity(activity, trip) for activity in activities]
    return sorted(
        results,
        key=lambda result: (
            -result.total_score,
            -result.coverage_count,
            result.activity_name.casefold(),
            result.activity_id,
        ),
    )
