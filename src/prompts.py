"""Prompts used for grounded itinerary narration."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from src.models import Activity, ItineraryPlan, TripRequest


NARRATION_SYSTEM_PROMPT = """
You are TripSync's travel editor. Write warm, useful narration for an itinerary
that has already been validated and scheduled by the application.

The schedule is immutable:
- Return exactly one day for every supplied itinerary day, in the same order.
- Return exactly one narrated activity for every scheduled activity, in the same
  order, using its exact activity_id.
- Keep an empty itinerary day empty.
- Never add, remove, rename, move, or duplicate an activity.

Ground every statement in the supplied trip, activity, and itinerary data. Do not
invent prices, opening hours, live availability, route times, booking status, or
facts about places. Clearly describe estimates and unresolved trade-offs as such.

Use an upbeat editorial travel tone without exaggeration. Explain how each activity
serves the group, mention relevant walking, budget, accessibility, reservation, or
pace considerations, and keep every explanation concise.
""".strip()


CHANGE_PROPOSAL_SYSTEM_PROMPT = """
You are TripSync's itinerary adjustment assistant. Respond to the organizer's
request with up to three concrete, user-reviewable options.

You may only propose either:
- replacing one activity already scheduled on its current day with one eligible
  replacement activity; or
- removing one scheduled activity and leaving that time open.

Never change another day, add a new activity without removing one, duplicate an
activity, use an ID outside `eligible_replacement_activities`, or apply a change
yourself. Scheduled activities may only be used as `remove_activity_id`; never
use a scheduled ID as `add_activity_id`. The organizer must approve a proposal
and the deterministic planner will validate it. Ground explanations in the
supplied trip, plan, and catalog.
Do not invent prices, opening hours, availability, route times, or booking facts.
Explain the relevant trade-offs concisely and warmly.
""".strip()


def _activity_context(activity: Activity) -> dict[str, Any]:
    return {
        "id": activity.id,
        "name": activity.name,
        "category": activity.category,
        "interests": activity.interests,
        "walking_level": activity.walking_level.value,
        "budget_level": activity.budget_level.value,
        "duration_hours": activity.duration_hours,
        "reservation_required": activity.reservation_required,
        "accessibility_notes": activity.accessibility_notes,
        "description": activity.description,
    }


def build_itinerary_narration_input(
    trip: TripRequest,
    activities: Sequence[Activity],
    plan: ItineraryPlan,
) -> list[dict[str, str]]:
    """Build the grounded Responses API input for one itinerary."""

    activity_by_id = {activity.id: activity for activity in activities}
    scheduled_ids = [
        scheduled.activity_id
        for day in plan.days
        for scheduled in day.activities
    ]
    missing_ids = [
        activity_id
        for activity_id in scheduled_ids
        if activity_id not in activity_by_id
    ]
    if missing_ids:
        missing_list = ", ".join(sorted(set(missing_ids)))
        raise ValueError(
            "itinerary references activities missing from narration "
            f"context: {missing_list}"
        )

    context = {
        "trip": trip.model_dump(mode="json"),
        "retrieved_scheduled_activities": [
            _activity_context(activity_by_id[activity_id])
            for activity_id in scheduled_ids
        ],
        "validated_itinerary": plan.model_dump(mode="json"),
    }
    user_prompt = (
        "Create the structured TripSync narrative from this grounded context:\n"
        f"{json.dumps(context, indent=2, sort_keys=True)}"
    )
    return [
        {"role": "system", "content": NARRATION_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def build_itinerary_change_input(
    trip: TripRequest,
    activities: Sequence[Activity],
    plan: ItineraryPlan,
    request: str,
) -> list[dict[str, str]]:
    """Build context for safe, catalog-grounded itinerary change options."""

    scheduled_ids = {
        scheduled.activity_id
        for day in plan.days
        for scheduled in day.activities
    }
    eligible_replacements = [
        activity for activity in activities if activity.id not in scheduled_ids
    ]
    context = {
        "organizer_request": request,
        "trip": trip.model_dump(mode="json"),
        "immutable_current_itinerary": plan.model_dump(mode="json"),
        "eligible_replacement_activities": [
            _activity_context(activity) for activity in eligible_replacements
        ],
    }
    return [
        {"role": "system", "content": CHANGE_PROPOSAL_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Return structured change proposals from this grounded context:\n"
                f"{json.dumps(context, indent=2, sort_keys=True)}"
            ),
        },
    ]
