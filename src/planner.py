"""Deterministic, grounded itinerary scheduling."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from src.models import (
    Activity,
    ItineraryDay,
    ItineraryPlan,
    ItinerarySource,
    ScheduledActivity,
    TripPace,
    TripRequest,
    UnscheduledActivity,
)
from src.scoring import GroupFitResult


TRANSITION_HOURS = 0.5


@dataclass(frozen=True)
class PaceRule:
    """Daily capacity for one trip pace."""

    capacity_hours: float
    max_activities: int


PACE_RULES = {
    TripPace.RELAXED: PaceRule(capacity_hours=4.0, max_activities=2),
    TripPace.BALANCED: PaceRule(capacity_hours=6.0, max_activities=3),
    TripPace.PACKED: PaceRule(capacity_hours=8.0, max_activities=4),
}


@dataclass
class _DayState:
    day_number: int
    activities: list[ScheduledActivity] = field(default_factory=list)
    activity_hours: float = 0.0
    transition_hours: float = 0.0

    @property
    def planned_hours(self) -> float:
        return round(self.activity_hours + self.transition_hours, 2)


def _unique_ids(activity_ids: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for activity_id in activity_ids:
        if activity_id not in seen:
            unique.append(activity_id)
            seen.add(activity_id)
    return unique


def _traveler_names(result: GroupFitResult | None) -> list[str]:
    if result is None:
        return []
    return [
        fit.traveler_name
        for fit in result.traveler_fits
        if fit.matched_interests or fit.must_do_match
    ]


def _scheduled_activity(
    activity: Activity,
    *,
    source: ItinerarySource,
    must_do_owners: Sequence[str],
    result: GroupFitResult | None,
) -> ScheduledActivity:
    owners = list(dict.fromkeys(must_do_owners))
    if owners:
        reason = f"Must-do for {' + '.join(owners)} from your shortlist."
    elif source == ItinerarySource.SHORTLIST:
        reason = "Saved in your trip shortlist."
    else:
        reason = "Added from the group-fit ranking to complete the day."

    return ScheduledActivity(
        activity_id=activity.id,
        activity_name=activity.name,
        duration_hours=activity.duration_hours,
        source=source,
        must_do_owners=owners,
        traveler_names=_traveler_names(result),
        reason=reason,
    )


def _fits_day(
    day: _DayState,
    activity: Activity,
    rule: PaceRule,
) -> bool:
    if len(day.activities) >= rule.max_activities:
        return False
    transition = TRANSITION_HOURS if day.activities else 0.0
    return (
        day.planned_hours + transition + activity.duration_hours
        <= rule.capacity_hours + 0.01
    )


def _place_activity(
    days: list[_DayState],
    activity: Activity,
    scheduled: ScheduledActivity,
    rule: PaceRule,
) -> bool:
    eligible_days = [
        day for day in days if _fits_day(day, activity, rule)
    ]
    if not eligible_days:
        return False

    day = min(
        eligible_days,
        key=lambda candidate: (
            candidate.planned_hours,
            len(candidate.activities),
            candidate.day_number,
        ),
    )
    if day.activities:
        day.transition_hours = round(
            day.transition_hours + TRANSITION_HOURS,
            2,
        )
    day.activities.append(scheduled)
    day.activity_hours = round(
        day.activity_hours + activity.duration_hours,
        2,
    )
    return True


def _unscheduled_reason(activity: Activity, rule: PaceRule) -> str:
    if activity.duration_hours > rule.capacity_hours:
        return (
            f"Needs {activity.duration_hours:g} hours, longer than the "
            f"{rule.capacity_hours:g}-hour {rule_label(rule)} day."
        )
    return (
        "Could not fit without exceeding the daily time or activity limit."
    )


def rule_label(rule: PaceRule) -> str:
    """Return the pace name for a configured rule."""

    return next(
        pace.value
        for pace, configured_rule in PACE_RULES.items()
        if configured_rule == rule
    )


def build_itinerary(
    trip: TripRequest,
    activities: Sequence[Activity],
    ranked_results: Sequence[GroupFitResult],
    selected_activity_ids: Sequence[str],
    *,
    must_do_owners_by_activity_id: Mapping[str, Sequence[str]] | None = None,
    auto_fill: bool = True,
) -> ItineraryPlan:
    """Build a stable itinerary from shortlisted and ranked activities.

    Shortlisted must-dos are placed first, followed by other shortlist choices.
    When enabled, auto-fill uses the existing group-fit ranking to fill remaining
    capacity. No activity is invented or scheduled more than once.
    """

    activity_by_id = {activity.id: activity for activity in activities}
    result_by_id = {
        result.activity_id: result for result in ranked_results
    }
    owners_by_id = must_do_owners_by_activity_id or {}
    rule = PACE_RULES[trip.pace]
    days = [
        _DayState(day_number=day_number)
        for day_number in range(1, trip.days + 1)
    ]

    selected_ids = _unique_ids(selected_activity_ids)
    known_selected_ids = [
        activity_id
        for activity_id in selected_ids
        if activity_id in activity_by_id
    ]
    selected_must_dos = [
        activity_id
        for activity_id in known_selected_ids
        if owners_by_id.get(activity_id)
    ]
    selected_regular = [
        activity_id
        for activity_id in known_selected_ids
        if activity_id not in selected_must_dos
    ]

    unscheduled = [
        UnscheduledActivity(
            activity_id=activity_id,
            activity_name=activity_id,
            reason="No longer exists in the retrieved activity catalog.",
        )
        for activity_id in selected_ids
        if activity_id not in activity_by_id
    ]

    for activity_id in [*selected_must_dos, *selected_regular]:
        activity = activity_by_id[activity_id]
        scheduled = _scheduled_activity(
            activity,
            source=ItinerarySource.SHORTLIST,
            must_do_owners=owners_by_id.get(activity_id, ()),
            result=result_by_id.get(activity_id),
        )
        if not _place_activity(days, activity, scheduled, rule):
            unscheduled.append(
                UnscheduledActivity(
                    activity_id=activity.id,
                    activity_name=activity.name,
                    reason=_unscheduled_reason(activity, rule),
                )
            )

    if auto_fill:
        scheduled_or_selected_ids = set(selected_ids)
        for result in ranked_results:
            activity_id = result.activity_id
            if (
                activity_id in scheduled_or_selected_ids
                or activity_id not in activity_by_id
            ):
                continue
            activity = activity_by_id[activity_id]
            scheduled = _scheduled_activity(
                activity,
                source=ItinerarySource.RECOMMENDATION,
                must_do_owners=(),
                result=result,
            )
            if _place_activity(days, activity, scheduled, rule):
                scheduled_or_selected_ids.add(activity_id)

    itinerary_days = [
        ItineraryDay(
            day_number=day.day_number,
            activities=day.activities,
            activity_hours=day.activity_hours,
            transition_hours=day.transition_hours,
            planned_hours=day.planned_hours,
            capacity_hours=rule.capacity_hours,
        )
        for day in days
    ]

    return ItineraryPlan(
        destination=trip.destination,
        country=trip.country,
        pace=trip.pace,
        auto_fill=auto_fill,
        days=itinerary_days,
        unscheduled=unscheduled,
    )
