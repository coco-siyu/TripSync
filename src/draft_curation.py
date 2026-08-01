"""Consistent, reviewable draft values for catalog candidates."""

from __future__ import annotations

from dataclasses import dataclass


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
