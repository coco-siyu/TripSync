"""Resolve traveler must-do text against grounded activity records."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from difflib import get_close_matches

from src.models import Activity, TravelerProfile


@dataclass(frozen=True)
class UnmatchedMustDo:
    """A traveler entry that could not be resolved to a known activity."""

    traveler_name: str
    entered_value: str
    suggested_activity_id: str | None = None
    suggested_activity_name: str | None = None


@dataclass(frozen=True)
class MustDoResolution:
    """Resolved owners and unresolved entries for a group."""

    owners_by_activity_id: dict[str, tuple[str, ...]]
    unmatched: tuple[UnmatchedMustDo, ...]


def canonicalize_activity_key(value: str) -> str:
    """Create a comparison key insensitive to spacing and punctuation."""

    return "".join(character for character in value.casefold() if character.isalnum())


def activity_match_keys(activity: Activity) -> frozenset[str]:
    """Return stable canonical identifiers accepted for one activity."""

    return frozenset(
        {
            canonicalize_activity_key(activity.id),
            canonicalize_activity_key(activity.name),
        }
    )


def matches_must_do(activity: Activity, entered_value: str) -> bool:
    """Return whether user text identifies an activity."""

    entered_key = canonicalize_activity_key(entered_value)
    return bool(entered_key) and entered_key in activity_match_keys(activity)


def resolve_must_dos(
    activities: Sequence[Activity],
    travelers: Sequence[TravelerProfile],
) -> MustDoResolution:
    """Resolve group must-dos once, preserving ownership and entry order."""

    activity_by_key: dict[str, Activity] = {}
    suggestion_name_by_key: dict[str, Activity] = {}
    for activity in activities:
        for key in activity_match_keys(activity):
            activity_by_key.setdefault(key, activity)
        suggestion_name_by_key[canonicalize_activity_key(activity.name)] = activity

    owners: dict[str, list[str]] = {}
    unmatched: list[UnmatchedMustDo] = []
    suggestion_keys = list(suggestion_name_by_key)

    for traveler in travelers:
        for entered_value in traveler.must_do_activities:
            entered_key = canonicalize_activity_key(entered_value)
            activity = activity_by_key.get(entered_key)
            if activity is not None:
                activity_owners = owners.setdefault(activity.id, [])
                if traveler.name not in activity_owners:
                    activity_owners.append(traveler.name)
                continue

            close_keys = get_close_matches(
                entered_key,
                suggestion_keys,
                n=1,
                cutoff=0.62,
            )
            suggestion = (
                suggestion_name_by_key[close_keys[0]] if close_keys else None
            )
            unmatched.append(
                UnmatchedMustDo(
                    traveler_name=traveler.name,
                    entered_value=entered_value,
                    suggested_activity_id=(
                        suggestion.id if suggestion is not None else None
                    ),
                    suggested_activity_name=(
                        suggestion.name if suggestion is not None else None
                    ),
                )
            )

    return MustDoResolution(
        owners_by_activity_id={
            activity_id: tuple(activity_owners)
            for activity_id, activity_owners in owners.items()
        },
        unmatched=tuple(unmatched),
    )
