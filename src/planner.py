"""Deterministic, grounded itinerary scheduling."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from math import asin, cos, radians, sin, sqrt

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
from src.proposals import ItineraryChangeProposal
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


@dataclass(frozen=True)
class ReplacementOutcome:
    """Result of an approved addition, replacement, or removal."""

    plan: ItineraryPlan
    removed_activity: ScheduledActivity | None
    replacement_activity: ScheduledActivity | None
    day_number: int


@dataclass(frozen=True)
class RouteLeg:
    """One estimated walking leg between two located itinerary stops."""

    from_activity_id: str
    to_activity_id: str
    distance_km: float
    walking_minutes: int


@dataclass(frozen=True)
class DayRouteSummary:
    """Small, explainable route-health summary for one itinerary day."""

    located_stops: int
    total_stops: int
    total_distance_km: float | None
    total_walking_minutes: int | None
    status: str
    message: str
    legs: tuple[RouteLeg, ...]


def _unique_ids(activity_ids: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for activity_id in activity_ids:
        if activity_id not in seen:
            unique.append(activity_id)
            seen.add(activity_id)
    return unique


def _distance_km(first: Activity, second: Activity) -> float | None:
    """Return a straight-line distance when both activities have coordinates."""

    if (
        first.latitude is None
        or first.longitude is None
        or second.latitude is None
        or second.longitude is None
    ):
        return None
    latitude_delta = radians(second.latitude - first.latitude)
    longitude_delta = radians(second.longitude - first.longitude)
    first_latitude = radians(first.latitude)
    second_latitude = radians(second.latitude)
    haversine = (
        sin(latitude_delta / 2) ** 2
        + cos(first_latitude)
        * cos(second_latitude)
        * sin(longitude_delta / 2) ** 2
    )
    return round(2 * 6371 * asin(sqrt(haversine)), 2)


def _walking_minutes(distance_km: float) -> int:
    """Use a conservative city-walking estimate, with a short-stop minimum."""

    return max(8, round(distance_km / 4.5 * 60))


def _route_order(
    activities: Sequence[ScheduledActivity],
    activity_by_id: Mapping[str, Activity],
) -> list[ScheduledActivity]:
    """Order known locations by proximity while leaving unknown stops stable.

    Must-dos retain first-stop priority. This intentionally avoids claiming an
    exact live route: it is a simple, predictable nearest-neighbour ordering.
    """

    located = [
        scheduled
        for scheduled in activities
        if (
            activity_by_id.get(scheduled.activity_id) is not None
            and activity_by_id[scheduled.activity_id].latitude is not None
            and activity_by_id[scheduled.activity_id].longitude is not None
        )
    ]
    if len(located) < 2:
        return list(activities)

    start = next(
        (scheduled for scheduled in located if scheduled.must_do_owners),
        located[0],
    )
    remaining = [scheduled for scheduled in located if scheduled != start]
    ordered = [start]
    while remaining:
        current = activity_by_id[ordered[-1].activity_id]
        next_stop = min(
            remaining,
            key=lambda scheduled: (
                _distance_km(current, activity_by_id[scheduled.activity_id])
                or float("inf"),
                scheduled.activity_name.casefold(),
            ),
        )
        ordered.append(next_stop)
        remaining.remove(next_stop)

    unlocated = [scheduled for scheduled in activities if scheduled not in located]
    return [*ordered, *unlocated]


def route_summary_for_day(
    day: ItineraryDay,
    activity_by_id: Mapping[str, Activity],
) -> DayRouteSummary:
    """Explain route coverage and walking distance for an ordered itinerary day."""

    activities = [
        activity_by_id.get(scheduled.activity_id)
        for scheduled in day.activities
    ]
    known = [activity for activity in activities if activity is not None]
    located_stops = sum(
        activity.latitude is not None and activity.longitude is not None
        for activity in known
    )
    legs: list[RouteLeg] = []
    for scheduled_first, scheduled_second, first, second in zip(
        day.activities,
        day.activities[1:],
        activities,
        activities[1:],
        strict=False,
    ):
        if first is None or second is None:
            continue
        distance = _distance_km(first, second)
        if distance is None:
            continue
        legs.append(
            RouteLeg(
                from_activity_id=scheduled_first.activity_id,
                to_activity_id=scheduled_second.activity_id,
                distance_km=distance,
                walking_minutes=_walking_minutes(distance),
            )
        )

    if len(day.activities) < 2:
        return DayRouteSummary(
            located_stops=located_stops,
            total_stops=len(day.activities),
            total_distance_km=0.0,
            total_walking_minutes=0,
            status="single_stop",
            message="One stop today, leaving plenty of room to explore nearby.",
            legs=tuple(),
        )
    if not legs:
        return DayRouteSummary(
            located_stops=located_stops,
            total_stops=len(day.activities),
            total_distance_km=None,
            total_walking_minutes=None,
            status="approximate",
            message=(
                "Route order is approximate because this catalog has no "
                "coordinates for these stops yet."
            ),
            legs=tuple(),
        )

    distance = round(sum(leg.distance_km for leg in legs), 1)
    minutes = sum(leg.walking_minutes for leg in legs)
    if located_stops < len(day.activities):
        status = "partial"
        message = (
            f"About {distance:g} km of known walking. Some stop locations "
            "are still missing, so this is a partial route estimate."
        )
    elif distance <= 1.5:
        status = "compact"
        message = f"Compact route: about {distance:g} km, roughly {minutes} minutes walking."
    elif distance <= 4:
        status = "walkable"
        message = f"Walkable route: about {distance:g} km, roughly {minutes} minutes walking."
    else:
        status = "spread_out"
        message = (
            f"This day is spread out: about {distance:g} km, roughly "
            f"{minutes} minutes walking. Consider swapping a stop if you want a calmer day."
        )
    return DayRouteSummary(
        located_stops=located_stops,
        total_stops=len(day.activities),
        total_distance_km=distance,
        total_walking_minutes=minutes,
        status=status,
        message=message,
        legs=tuple(legs),
    )


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
    excluded_activity_ids: Sequence[str] = (),
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
    excluded_ids = set(excluded_activity_ids)
    rule = PACE_RULES[trip.pace]
    days = [
        _DayState(day_number=day_number)
        for day_number in range(1, trip.days + 1)
    ]

    selected_ids = [
        activity_id
        for activity_id in _unique_ids(selected_activity_ids)
        if activity_id not in excluded_ids
    ]
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
                or activity_id in excluded_ids
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

    for day in days:
        day.activities = _route_order(day.activities, activity_by_id)

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


def replace_itinerary_activity(
    plan: ItineraryPlan,
    rejected_activity_id: str,
    activities: Sequence[Activity],
    ranked_results: Sequence[GroupFitResult],
    *,
    excluded_activity_ids: Sequence[str] = (),
    must_do_owners_by_activity_id: Mapping[str, Sequence[str]] | None = None,
) -> ReplacementOutcome:
    """Replace one activity without changing any other itinerary day."""

    activity_by_id = {activity.id: activity for activity in activities}
    result_by_id = {
        result.activity_id: result for result in ranked_results
    }
    owners_by_id = must_do_owners_by_activity_id or {}
    target_day: ItineraryDay | None = None
    target_position: int | None = None
    removed_activity: ScheduledActivity | None = None

    for day in plan.days:
        for position, scheduled in enumerate(day.activities):
            if scheduled.activity_id == rejected_activity_id:
                target_day = day
                target_position = position
                removed_activity = scheduled
                break
        if removed_activity is not None:
            break

    if (
        target_day is None
        or target_position is None
        or removed_activity is None
    ):
        raise ValueError(
            f"activity {rejected_activity_id!r} is not in the itinerary"
        )

    remaining_activities = [
        activity
        for activity in target_day.activities
        if activity.activity_id != rejected_activity_id
    ]
    scheduled_ids = {
        activity.activity_id
        for day in plan.days
        for activity in day.activities
        if activity.activity_id != rejected_activity_id
    }
    excluded_ids = {
        *excluded_activity_ids,
        rejected_activity_id,
    }

    replacement: ScheduledActivity | None = None
    transition_hours_with_replacement = (
        TRANSITION_HOURS * len(remaining_activities)
    )
    remaining_activity_hours = sum(
        activity.duration_hours for activity in remaining_activities
    )

    for result in ranked_results:
        candidate_id = result.activity_id
        if (
            candidate_id in scheduled_ids
            or candidate_id in excluded_ids
            or candidate_id not in activity_by_id
        ):
            continue
        candidate = activity_by_id[candidate_id]
        planned_hours = (
            remaining_activity_hours
            + candidate.duration_hours
            + transition_hours_with_replacement
        )
        if planned_hours > target_day.capacity_hours + 0.01:
            continue

        replacement = _scheduled_activity(
            candidate,
            source=ItinerarySource.RECOMMENDATION,
            must_do_owners=owners_by_id.get(candidate_id, ()),
            result=result_by_id.get(candidate_id),
        ).model_copy(
            update={
                "reason": (
                    "Replacement selected from the group-fit ranking after "
                    f"removing {removed_activity.activity_name}."
                )
            }
        )
        break

    updated_activities = list(remaining_activities)
    if replacement is not None:
        updated_activities.insert(target_position, replacement)
    updated_activities = _route_order(updated_activities, activity_by_id)

    activity_hours = round(
        sum(activity.duration_hours for activity in updated_activities),
        2,
    )
    transition_hours = round(
        TRANSITION_HOURS * max(0, len(updated_activities) - 1),
        2,
    )
    updated_day = ItineraryDay(
        day_number=target_day.day_number,
        activities=updated_activities,
        activity_hours=activity_hours,
        transition_hours=transition_hours,
        planned_hours=round(activity_hours + transition_hours, 2),
        capacity_hours=target_day.capacity_hours,
    )
    updated_days = [
        (
            updated_day.model_dump(mode="json")
            if day.day_number == target_day.day_number
            else day.model_dump(mode="json")
        )
        for day in plan.days
    ]
    updated_plan = ItineraryPlan.model_validate(
        {
            **plan.model_dump(mode="json"),
            "days": updated_days,
        }
    )

    return ReplacementOutcome(
        plan=updated_plan,
        removed_activity=removed_activity,
        replacement_activity=replacement,
        day_number=target_day.day_number,
    )


def apply_itinerary_change_proposal(
    plan: ItineraryPlan,
    proposal: ItineraryChangeProposal,
    activities: Sequence[Activity],
    ranked_results: Sequence[GroupFitResult],
    *,
    must_do_owners_by_activity_id: Mapping[str, Sequence[str]] | None = None,
    allow_pace_override: bool = False,
) -> ReplacementOutcome:
    """Apply one approved proposal, requiring consent for pace overrides."""

    activity_by_id = {activity.id: activity for activity in activities}
    result_by_id = {result.activity_id: result for result in ranked_results}
    owners_by_id = must_do_owners_by_activity_id or {}
    target_day = next(
        (day for day in plan.days if day.day_number == proposal.day_number),
        None,
    )
    if target_day is None:
        raise ValueError(f"Day {proposal.day_number} is not in the itinerary")

    target_position: int | None = None
    removed_activity: ScheduledActivity | None = None
    if proposal.remove_activity_id is not None:
        target_position = next(
            (
                position
                for position, activity in enumerate(target_day.activities)
                if activity.activity_id == proposal.remove_activity_id
            ),
            None,
        )
        if target_position is None:
            raise ValueError(
                f"{proposal.remove_activity_id} is not scheduled on Day "
                f"{proposal.day_number}"
            )
        removed_activity = target_day.activities[target_position]
    scheduled_ids = {
        activity.activity_id
        for day in plan.days
        for activity in day.activities
    }

    replacement: ScheduledActivity | None = None
    if proposal.add_activity_id is not None:
        if proposal.add_activity_id not in activity_by_id:
            raise ValueError("The suggested replacement is not in the catalog")
        if proposal.add_activity_id in scheduled_ids:
            raise ValueError("The suggested replacement is already scheduled")
        candidate = activity_by_id[proposal.add_activity_id]
        replacement = _scheduled_activity(
            candidate,
            source=ItinerarySource.RECOMMENDATION,
            must_do_owners=owners_by_id.get(candidate.id, ()),
            result=result_by_id.get(candidate.id),
        ).model_copy(
            update={
                "reason": "Applied from an organizer-approved TripSync suggestion."
            }
        )

    updated_activities = [
        activity
        for activity in target_day.activities
        if activity.activity_id != proposal.remove_activity_id
    ]
    if replacement is not None:
        if target_position is None:
            updated_activities.append(replacement)
        else:
            updated_activities.insert(target_position, replacement)

    updated_activities = _route_order(updated_activities, activity_by_id)

    rule = PACE_RULES[plan.pace]
    activity_hours = round(
        sum(activity.duration_hours for activity in updated_activities),
        2,
    )
    transition_hours = round(
        TRANSITION_HOURS * max(0, len(updated_activities) - 1),
        2,
    )
    planned_hours = round(activity_hours + transition_hours, 2)
    exceeds_activity_limit = len(updated_activities) > rule.max_activities
    exceeds_hour_limit = planned_hours > target_day.capacity_hours + 0.01
    if (exceeds_activity_limit or exceeds_hour_limit) and not allow_pace_override:
        pace_issues: list[str] = []
        if exceeds_activity_limit:
            pace_issues.append(
                f"would have more than {rule.max_activities} activities "
                f"for a {plan.pace.value} pace"
            )
        if exceeds_hour_limit:
            pace_issues.append(
                f"would be {planned_hours:g} hours against its "
                f"{target_day.capacity_hours:g}-hour limit"
            )
        raise ValueError(
            f"Day {proposal.day_number} " + " and ".join(pace_issues) + "."
        )

    updated_day = ItineraryDay(
        day_number=target_day.day_number,
        activities=updated_activities,
        activity_hours=activity_hours,
        transition_hours=transition_hours,
        planned_hours=planned_hours,
        capacity_hours=target_day.capacity_hours,
        pace_override_approved=(
            target_day.pace_override_approved
            or exceeds_activity_limit
            or exceeds_hour_limit
        ),
    )
    updated_plan = ItineraryPlan.model_validate(
        {
            **plan.model_dump(mode="json"),
            "days": [
                (
                    updated_day.model_dump(mode="json")
                    if day.day_number == target_day.day_number
                    else day.model_dump(mode="json")
                )
                for day in plan.days
            ],
        }
    )
    return ReplacementOutcome(
        plan=updated_plan,
        removed_activity=removed_activity,
        replacement_activity=replacement,
        day_number=target_day.day_number,
    )
