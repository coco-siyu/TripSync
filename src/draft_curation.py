"""Consistent, reviewable draft values for catalog candidates."""

from __future__ import annotations

from dataclasses import dataclass


AUTO_EXCLUDED_TYPE_TERMS = (
    "hotel",
    "hostel",
    "guest house",
    "guesthouse",
    "apartment building",
    "resort",
    "airport",
    "aerodrome",
    "airfield",
    "railway station",
    "bus station",
)


@dataclass(frozen=True)
class ActivityDraft:
    category: str
    category_tags: tuple[str, ...]
    interests: tuple[str, ...]
    walking_level: str
    budget_level: str
    duration_hours: float
    indoor: bool
    reservation_required: bool


def draft_activity(candidate: dict) -> ActivityDraft:
    """Map common Wikidata types to conservative, editable trip-planning drafts."""

    types = " ".join(candidate.get("wikidata_types", [])).casefold()
    if any(word in types for word in ("museum", "art gallery", "library")):
        return ActivityDraft("museum", ("cultural",), ("art", "culture", "history"), "moderate", "moderate", 2.0, True, True)
    if any(word in types for word in ("cathedral", "basilica", "church")):
        return ActivityDraft("historic_site", ("religious_site",), ("architecture", "history", "religion"), "low", "free", 1.0, True, False)
    if any(word in types for word in ("park", "garden")):
        return ActivityDraft("park", ("outdoor",), ("nature", "relaxation", "photography"), "moderate", "free", 1.5, False, False)
    if any(word in types for word in ("castle", "palace", "archaeological")):
        return ActivityDraft("historic_site", ("architecture",), ("history", "architecture", "culture"), "moderate", "moderate", 2.0, True, True)
    return ActivityDraft("landmark", ("outdoor",), ("history", "architecture", "photography"), "low", "free", 1.0, False, False)


def automatic_curation_reason(candidate: dict) -> str | None:
    """Return why an imported record must not be auto-promoted, if any."""

    types = " ".join(candidate.get("wikidata_types", [])).casefold()
    matched = next((term for term in AUTO_EXCLUDED_TYPE_TERMS if term in types), None)
    if matched:
        return f"Auto-skip: Wikidata type indicates {matched}, not a visitor activity."
    if not types.strip():
        return "Auto-skip: Wikidata supplied no place type to classify."
    return None


def automatic_activity_fields(candidate: dict, city: str) -> dict | None:
    """Create conservative Pydantic-ready fields for a safely classified place.

    The result intentionally avoids live claims about access, prices, or opening
    hours. It is a batch-review draft with a source URL, not an assertion that a
    venue is currently open or bookable.
    """

    if automatic_curation_reason(candidate):
        return None
    draft = draft_activity(candidate)
    name = candidate["name"].strip()
    return {
        "name": name,
        "category": draft.category,
        "category_tags": list(draft.category_tags),
        "interests": list(draft.interests),
        "walking_level": draft.walking_level,
        "budget_level": draft.budget_level,
        "duration_hours": draft.duration_hours,
        "indoor": draft.indoor,
        "family_friendly": True,
        "reservation_required": draft.reservation_required,
        "accessibility_notes": "Verify current accessibility and visitor information before visiting.",
        "description": f"Visit {name}, a curated {draft.category.replace('_', ' ')} experience in {city}.",
        "source_url": candidate["source_url"],
    }
