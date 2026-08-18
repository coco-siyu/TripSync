"""Consistent, reviewable draft values for catalog candidates."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Literal


REJECT_TYPE_TERMS = (
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
    "metro",
    "subway",
    "tram stop",
    "ferry terminal",
    "railway",
    "organisation",
    "organization",
    "company",
    "hospital",
    "prison",
    "military base",
    "power station",
    "human settlement",
    "administrative territorial entity",
    "municipal arrondissement",
    "locality",
)

REJECT_NAME_TERMS = (
    " airport",
    " attacks",
    " attack",
    " massacre",
    " disaster",
    " earthquake",
    " terrorist",
    " organisation",
    " organization",
    " metro",
)

REVIEW_TYPE_TERMS = (
    "university",
    "college",
    "library",
    "stadium",
    "cemetery",
    "island",
    "canal",
    "river",
    "neighbourhood",
    "neighborhood",
    "district",
    "quarter",
    "artwork",
    "sculpture",
    "annual event",
    "festival",
    "carnival",
)

AUTO_PUBLISH_TYPE_TERMS = (
    "museum",
    "art gallery",
    "palace",
    "castle",
    "cathedral",
    "basilica",
    "church",
    "historic site",
    "archaeological",
    "monument",
    "landmark",
    "bridge",
    "garden",
    "park",
    "market",
    "theatre",
    "theater",
    "opera house",
    "tower",
    "fountain",
    "square",
    "triumphal arch",
)

# These terms frequently describe well-known visitor attractions in large
# cities, but are broader than a museum or cathedral.  We only auto-publish
# them when Wikidata also supplies an English Wikipedia article; otherwise
# they remain in the review queue.
EVIDENCED_AUTO_PUBLISH_TYPE_TERMS = (
    "tourist attraction",
    "listed building",
    "cultural heritage",
    "heritage site",
    "historic building",
    "historic house",
    "house museum",
    "royal residence",
    "place of worship",
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


@dataclass(frozen=True)
class CandidateQualityDecision:
    """A transparent, deterministic decision for an imported place."""

    outcome: Literal["reject", "auto_publish", "review"]
    confidence: float
    reason: str


def _normalise(value: str) -> str:
    """Make simple source labels comparable without losing the original value."""

    plain = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return " ".join(plain.casefold().split())


def _matches_any(text: str, terms: tuple[str, ...]) -> str | None:
    return next((term for term in terms if term in text), None)


def classify_candidate(candidate: dict) -> CandidateQualityDecision:
    """Classify source candidates before a person needs to inspect them.

    The gate deliberately makes no claims about opening hours, ticket prices, or
    access. It only decides whether a Wikidata record resembles a visitor
    activity, clearly does not, or needs a human to make that distinction.
    """

    name = _normalise(str(candidate.get("name", "")))
    types = _normalise(" ".join(str(item) for item in candidate.get("wikidata_types", [])))
    identifier = str(candidate.get("wikidata_id", ""))
    combined = f"{name} {types}"

    if re.fullmatch(r"q\d+", name):
        return CandidateQualityDecision("reject", 0.99, "Source record has no usable visitor-facing name.")
    matched = _matches_any(types, REJECT_TYPE_TERMS)
    if matched:
        return CandidateQualityDecision("reject", 0.99, f"Type indicates {matched}, not a visitor activity.")
    matched = _matches_any(combined, REJECT_NAME_TERMS)
    if matched:
        return CandidateQualityDecision("reject", 0.98, "Name describes transport, an organisation, or a tragic event.")
    if not types:
        return CandidateQualityDecision("reject", 0.95, "Wikidata supplied no type to verify visitor relevance.")
    matched = _matches_any(types, AUTO_PUBLISH_TYPE_TERMS)
    if matched:
        has_wikipedia = bool(candidate.get("wikipedia_url"))
        confidence = 0.95 if has_wikipedia else 0.86
        evidence = "with an English Wikipedia record" if has_wikipedia else "with a clear Wikidata type"
        return CandidateQualityDecision("auto_publish", confidence, f"Recognised {matched} {evidence}.")
    matched = _matches_any(types, EVIDENCED_AUTO_PUBLISH_TYPE_TERMS)
    if matched and candidate.get("wikipedia_url"):
        return CandidateQualityDecision(
            "auto_publish",
            0.84,
            f"Recognised {matched} with an English Wikipedia record.",
        )
    matched = _matches_any(types, REVIEW_TYPE_TERMS)
    if matched:
        return CandidateQualityDecision("review", 0.65, f"{matched.title()} needs context before it becomes a trip activity.")
    return CandidateQualityDecision("review", 0.5, "Type is not yet in TripSync's trusted attraction rules.")


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

    decision = classify_candidate(candidate)
    if decision.outcome == "auto_publish":
        return None
    prefix = "Auto-skip" if decision.outcome == "reject" else "Needs review"
    return f"{prefix}: {decision.reason}"


def automatic_activity_fields(candidate: dict, city: str) -> dict | None:
    """Create conservative Pydantic-ready fields for a safely classified place.

    The result intentionally avoids live claims about access, prices, or opening
    hours. It is a batch-review draft with a source URL, not an assertion that a
    venue is currently open or bookable.
    """

    if classify_candidate(candidate).outcome != "auto_publish":
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
        "official_url": candidate.get("official_url"),
        "wikipedia_url": candidate.get("wikipedia_url"),
    }
