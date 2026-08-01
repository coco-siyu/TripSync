"""Curate reviewed Wikidata candidates into validated TripSync activities."""

from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

from src.models import Activity


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_DIRECTORY = REPOSITORY_ROOT / "data" / "candidates"
ACTIVITY_CATALOG_PATH = REPOSITORY_ROOT / "data" / "activities.json"
SAMPLE_ACTIVITY_PATH = REPOSITORY_ROOT / "data" / "sample_activities.json"


def candidate_files() -> list[Path]:
    """Return saved candidate batches, newest first."""

    return sorted(CANDIDATE_DIRECTORY.glob("*_wikidata_candidates.json"), reverse=True)


def load_candidate_batch(path: Path) -> dict[str, Any]:
    """Load the small, review-only document produced by the importer."""

    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("candidates"), list):
        raise ValueError("Candidate file must contain a candidates list")
    if not isinstance(document.get("city"), str) or not isinstance(document.get("country"), str):
        raise ValueError("Candidate file must identify its city and country")
    return document


def activity_id(city: str, name: str) -> str:
    """Create the stable, model-compatible identifier for a curated activity."""

    plain = unicodedata.normalize("NFKD", f"{city} {name}").encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "_", plain.casefold()).strip("_")
    if not slug:
        raise ValueError("Activity name must contain letters or numbers")
    return slug


def build_activity(candidate: dict[str, Any], city: str, country: str, fields: dict[str, Any]) -> Activity:
    """Merge reviewed form fields with provenance and validate with Pydantic."""

    name = fields["name"]
    return Activity.model_validate({
        "id": activity_id(city, name), "name": name, "city": city, "country": country,
        "category": fields["category"], "category_tags": fields.get("category_tags", []), "interests": fields["interests"],
        "walking_level": fields["walking_level"], "budget_level": fields["budget_level"],
        "duration_hours": fields["duration_hours"], "indoor": fields["indoor"],
        "family_friendly": fields["family_friendly"],
        "accessibility_notes": fields["accessibility_notes"],
        "reservation_required": fields["reservation_required"],
        "description": fields["description"],
        "source_url": fields.get("source_url") or candidate["source_url"],
    })


def load_curated_activities(path: Path = ACTIVITY_CATALOG_PATH) -> list[Activity]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [Activity.model_validate(item) for item in raw]


def save_activity(activity: Activity, path: Path = ACTIVITY_CATALOG_PATH) -> None:
    """Append a validated activity atomically, refusing duplicate IDs."""

    activities = load_curated_activities(path)
    if activity.id in {existing.id for existing in activities}:
        raise ValueError(f"{activity.name} is already in the curated catalog")
    activities.append(activity)
    _write_activities(activities, path)


def _write_activities(activities: list[Activity], path: Path) -> None:
    """Atomically replace the canonical activity catalog."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps([entry.model_dump(mode="json") for entry in activities], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def consolidate_sample_activities(path: Path = ACTIVITY_CATALOG_PATH) -> int:
    """Move legacy Rome sample records into the one canonical catalog.

    Existing catalog records are retained unless they duplicate a sample ID.
    In that case the original sample record wins because it is the richer,
    established version of that same attraction.
    """

    sample_raw = json.loads(SAMPLE_ACTIVITY_PATH.read_text(encoding="utf-8"))
    sample = [Activity.model_validate(item) for item in sample_raw]
    existing = load_curated_activities(path)
    merged = {activity.id: activity for activity in existing}
    merged.update({activity.id: activity for activity in sample})
    activities = sorted(merged.values(), key=lambda activity: (activity.city, activity.name))
    _write_activities(activities, path)
    return len(activities)
