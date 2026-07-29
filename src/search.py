"""Deterministic text retrieval for grounded activity candidates."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from src.models import Activity, TripRequest
from src.must_dos import canonicalize_activity_key, matches_must_do


@dataclass(frozen=True)
class RetrievedActivity:
    """One activity plus the evidence that caused it to be retrieved."""

    activity_id: str
    relevance_score: int
    matched_terms: tuple[str, ...]
    matched_travelers: tuple[str, ...]
    must_do_owners: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalResponse:
    """Destination filtering and ranked text-retrieval results."""

    results: tuple[RetrievedActivity, ...]
    destination_activity_ids: tuple[str, ...]
    used_fallback: bool = False


def normalize_search_text(value: str) -> str:
    """Normalize free text without allowing partial-word matches."""

    separated = "".join(
        character if character.isalnum() else " "
        for character in value.casefold()
    )
    return " ".join(separated.split())


def _contains_phrase(document: str, phrase: str) -> bool:
    normalized_phrase = normalize_search_text(phrase)
    if not normalized_phrase:
        return False
    return f" {normalized_phrase} " in f" {document} "


def _activity_document(activity: Activity) -> str:
    fields = [
        activity.name,
        activity.category.replace("_", " "),
        *activity.interests,
        activity.description,
    ]
    return normalize_search_text(" ".join(fields))


def _destination_matches(activity: Activity, trip: TripRequest) -> bool:
    return (
        canonicalize_activity_key(activity.city)
        == canonicalize_activity_key(trip.destination)
        and canonicalize_activity_key(activity.country)
        == canonicalize_activity_key(trip.country)
    )


def _unique_destination_activities(
    activities: Sequence[Activity],
    trip: TripRequest,
) -> list[Activity]:
    """Filter by destination and prevent duplicate activity identifiers."""

    matches: list[Activity] = []
    seen_ids: set[str] = set()
    for activity in activities:
        if activity.id in seen_ids or not _destination_matches(activity, trip):
            continue
        matches.append(activity)
        seen_ids.add(activity.id)
    return matches


def _interest_owners(trip: TripRequest) -> dict[str, list[str]]:
    owners: dict[str, list[str]] = {}
    for traveler in trip.travelers:
        for interest in traveler.interests:
            normalized_interest = normalize_search_text(interest)
            if not normalized_interest:
                continue
            interest_owners = owners.setdefault(normalized_interest, [])
            if traveler.name not in interest_owners:
                interest_owners.append(traveler.name)
    return owners


def _retrieve_one(
    activity: Activity,
    trip: TripRequest,
    interest_owners: dict[str, list[str]],
) -> RetrievedActivity | None:
    document = _activity_document(activity)
    matched_terms = tuple(
        term
        for term in interest_owners
        if _contains_phrase(document, term)
    )
    matched_owner_names = {
        owner
        for term in matched_terms
        for owner in interest_owners[term]
    }
    matched_travelers = tuple(
        traveler.name
        for traveler in trip.travelers
        if traveler.name in matched_owner_names
    )
    must_do_owners = tuple(
        traveler.name
        for traveler in trip.travelers
        if any(
            matches_must_do(activity, must_do)
            for must_do in traveler.must_do_activities
        )
    )

    if not matched_terms and not must_do_owners:
        return None

    reasons: list[str] = []
    if must_do_owners:
        reasons.append(f"Must-do for {' + '.join(must_do_owners)}")
    if matched_terms:
        reasons.append(
            f"Matches group interests: {', '.join(matched_terms)}"
        )

    return RetrievedActivity(
        activity_id=activity.id,
        relevance_score=(
            100 * bool(must_do_owners)
            + 10 * len(matched_travelers)
            + len(matched_terms)
        ),
        matched_terms=matched_terms,
        matched_travelers=matched_travelers,
        must_do_owners=must_do_owners,
        reasons=tuple(reasons),
    )


def retrieve_activities(
    activities: Sequence[Activity],
    trip: TripRequest,
    *,
    limit: int | None = None,
) -> RetrievalResponse:
    """Retrieve destination activities from group interests and must-dos.

    Recognized must-dos are always retained, even when that means returning
    more items than a requested limit. If no preference text matches, all
    destination activities are returned as an explicit fallback.
    """

    if limit is not None and limit < 1:
        raise ValueError("retrieval limit must be at least 1")

    destination_activities = _unique_destination_activities(activities, trip)
    destination_ids = tuple(
        activity.id for activity in destination_activities
    )
    owners_by_interest = _interest_owners(trip)

    retrieved = [
        result
        for activity in destination_activities
        if (
            result := _retrieve_one(
                activity,
                trip,
                owners_by_interest,
            )
        )
        is not None
    ]
    retrieved.sort(
        key=lambda result: (
            -result.relevance_score,
            result.activity_id,
        )
    )

    used_fallback = False
    if not retrieved and destination_activities:
        used_fallback = True
        retrieved = [
            RetrievedActivity(
                activity_id=activity.id,
                relevance_score=0,
                matched_terms=(),
                matched_travelers=(),
                must_do_owners=(),
                reasons=(
                    "No direct preference match; included from the "
                    "destination catalog.",
                ),
            )
            for activity in destination_activities
        ]

    if limit is not None:
        must_do_count = sum(bool(result.must_do_owners) for result in retrieved)
        retrieved = retrieved[: max(limit, must_do_count)]

    return RetrievalResponse(
        results=tuple(retrieved),
        destination_activity_ids=destination_ids,
        used_fallback=used_fallback,
    )
