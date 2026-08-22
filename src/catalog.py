"""Curate reviewed Wikidata candidates into validated TripSync activities."""

from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.draft_curation import automatic_activity_fields, automatic_curation_reason, classify_candidate
from src.models import Activity
from src.supabase_store import delete_in, is_configured, select, upsert_many


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_DIRECTORY = REPOSITORY_ROOT / "data" / "candidates"
ACTIVITY_CATALOG_PATH = REPOSITORY_ROOT / "data" / "activities.json"
SAMPLE_ACTIVITY_PATH = REPOSITORY_ROOT / "data" / "sample_activities.json"
SUPABASE_CATALOG_TABLE = "catalog_activities"
SUPABASE_DESTINATION_VIEW = "catalog_destinations"
SUPABASE_REVIEW_TABLE = "catalog_review_candidates"
REVIEW_CANDIDATES_PATH = REPOSITORY_ROOT / "data" / "review_candidates.json"


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
        "official_url": fields.get("official_url") or candidate.get("official_url"),
        "wikipedia_url": fields.get("wikipedia_url") or candidate.get("wikipedia_url"),
        "wikidata_id": candidate.get("wikidata_id"),
        "address": fields.get("address"),
        "opening_hours": fields.get("opening_hours"),
        "osm_url": fields.get("osm_url"),
        "latitude": fields.get("latitude", candidate.get("latitude")),
        "longitude": fields.get("longitude", candidate.get("longitude")),
    })


def _uses_supabase(path: Path) -> bool:
    """Use the shared catalog only when its optional client is available.

    Keeping this check local makes a developer checkout with configured secrets
    but without ``pip install -r requirements.txt`` safely use the JSON fallback.
    Streamlit Cloud installs the declared dependency and therefore uses Supabase.
    """

    if path != ACTIVITY_CATALOG_PATH or not is_configured():
        return False
    try:
        from supabase import create_client  # noqa: F401
    except ImportError:
        return False
    return True


def _load_local_activities(path: Path) -> list[Activity]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [Activity.model_validate(item) for item in raw]


def _remote_rows(activities: list[Activity]) -> list[dict[str, Any]]:
    return [
        {
            "activity_id": activity.id,
            "activity_json": activity.model_dump(mode="json"),
        }
        for activity in activities
    ]


def _deduplicate_destinations(
    destinations: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    unique: dict[tuple[str, str], tuple[str, str]] = {}
    for raw_city, raw_country in destinations:
        city = raw_city.strip()
        country = raw_country.strip()
        if not city or not country:
            raise ValueError("Catalog destinations require a city and country")
        unique.setdefault((city.casefold(), country.casefold()), (city, country))
    return sorted(
        unique.values(),
        key=lambda item: (item[0].casefold(), item[1].casefold()),
    )


def _local_catalog_destinations(path: Path) -> list[tuple[str, str]]:
    return _deduplicate_destinations(
        [(activity.city, activity.country) for activity in _load_local_activities(path)]
    )


def load_packaged_catalog_destinations(
    path: Path = ACTIVITY_CATALOG_PATH,
) -> list[tuple[str, str]]:
    """Load the bundled destination index without contacting Supabase."""

    return _local_catalog_destinations(path)


def _destinations_from_rows(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    destinations: list[tuple[str, str]] = []
    for row in rows:
        city = row.get("city")
        country = row.get("country")
        if not isinstance(city, str) or not isinstance(country, str):
            raise ValueError("Catalog destination query returned invalid data")
        destinations.append((city, country))
    return _deduplicate_destinations(destinations)


def load_catalog_destinations(
    path: Path = ACTIVITY_CATALOG_PATH,
) -> list[tuple[str, str]]:
    """Load only published city/country pairs for destination autocomplete.

    The Supabase view derives its rows from the existing activity JSON, so it
    covers records published before the view existed and stays synchronized
    with future catalog inserts, updates, and deletions. A local checkout or a
    database that has not applied the latest schema safely uses the packaged
    catalog index instead.
    """

    if not _uses_supabase(path):
        return _local_catalog_destinations(path)
    try:
        rows = select(
            SUPABASE_DESTINATION_VIEW,
            order="city",
            columns="city,country",
            desc=False,
        )
        if rows:
            return _destinations_from_rows(rows)
    except Exception:  # noqa: BLE001 - projection supports pre-migration databases
        pass

    # Existing Supabase projects may run the new app before schema.sql is
    # reapplied. PostgREST can project only two JSON fields, preserving every
    # already-published destination without downloading full activity records.
    try:
        rows = select(
            SUPABASE_CATALOG_TABLE,
            order="activity_id",
            columns="activity_json->>city,activity_json->>country",
            desc=False,
        )
        if rows:
            return _destinations_from_rows(rows)
    except Exception:  # noqa: BLE001 - local index keeps the first page usable
        pass
    return _local_catalog_destinations(path)


def review_candidate_id(candidate: dict[str, Any], city: str) -> str:
    """Create a stable queue key for one ambiguous source record."""

    source_id = str(candidate.get("wikidata_id", "")).strip()
    if not source_id:
        source_id = activity_id(city, str(candidate.get("name", "")))
    return f"{activity_id(city, city)}:{source_id.casefold()}"


def _load_local_review_candidates(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("Review candidate store must contain a list")
    return [row for row in rows if isinstance(row, dict)]


def _write_local_review_candidates(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def save_review_candidates(
    candidates: list[dict[str, Any]],
    city: str,
    country: str,
    path: Path = REVIEW_CANDIDATES_PATH,
) -> int:
    """Persist only ambiguous places for optional later review.

    Clear rejects never enter this queue. The shared Supabase store is used for
    production ingestion; the JSON path is only a local-development fallback.
    """

    timestamp = datetime.now(UTC).isoformat()
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        decision = classify_candidate(candidate)
        if decision.outcome != "review":
            continue
        rows.append(
            {
                "review_id": review_candidate_id(candidate, city),
                "city": city,
                "country": country,
                "candidate_json": candidate,
                "reason": decision.reason,
                "confidence": decision.confidence,
                "updated_at": timestamp,
            }
        )
    if not rows:
        return 0
    if path == REVIEW_CANDIDATES_PATH and _uses_supabase(ACTIVITY_CATALOG_PATH):
        upsert_many(SUPABASE_REVIEW_TABLE, rows, conflict="review_id")
        return len(rows)

    existing = {row.get("review_id"): row for row in _load_local_review_candidates(path)}
    existing.update({row["review_id"]: row for row in rows})
    _write_local_review_candidates(list(existing.values()), path)
    return len(rows)


def load_review_candidates(path: Path = REVIEW_CANDIDATES_PATH) -> list[dict[str, Any]]:
    """Load the optional queue of ambiguous, unpublished source records."""

    if path == REVIEW_CANDIDATES_PATH and _uses_supabase(ACTIVITY_CATALOG_PATH):
        return select(SUPABASE_REVIEW_TABLE, order="updated_at")
    return _load_local_review_candidates(path)


def delete_review_candidates(
    review_ids: list[str], path: Path = REVIEW_CANDIDATES_PATH
) -> int:
    """Remove records after a curator publishes or dismisses them."""

    ids = {review_id for review_id in review_ids if review_id}
    if not ids:
        return 0
    if path == REVIEW_CANDIDATES_PATH and _uses_supabase(ACTIVITY_CATALOG_PATH):
        delete_in(SUPABASE_REVIEW_TABLE, "review_id", list(ids))
        return len(ids)
    existing = _load_local_review_candidates(path)
    retained = [row for row in existing if row.get("review_id") not in ids]
    removed = len(existing) - len(retained)
    if removed:
        _write_local_review_candidates(retained, path)
    return removed


def _try_sync_embeddings(activities: list[Activity]) -> None:
    """Enrich changed shared records without making catalog writes dependent on AI."""

    try:
        from src.embeddings import EmbeddingUnavailable, ensure_activity_embeddings

        ensure_activity_embeddings(activities)
    except (EmbeddingUnavailable, ImportError):
        # A missing API key, a temporary API error, or a developer's local JSON
        # catalog should never prevent a validated activity from publishing.
        return


def load_curated_activities(path: Path = ACTIVITY_CATALOG_PATH) -> list[Activity]:
    """Load the shared catalog, seeding it from the packaged file once if empty."""

    if not _uses_supabase(path):
        return _load_local_activities(path)
    try:
        rows = select(SUPABASE_CATALOG_TABLE, order="activity_id")
        if rows:
            return [Activity.model_validate(row["activity_json"]) for row in rows]
        seed = _load_local_activities(path)
        upsert_many(SUPABASE_CATALOG_TABLE, _remote_rows(seed), conflict="activity_id")
        return seed
    except Exception:  # noqa: BLE001 - a local, usable catalog is the safe fallback
        # A transient network problem must not stop planning. Writes remain
        # explicit and therefore still surface an actionable Supabase error.
        return _load_local_activities(path)


def save_activity(activity: Activity, path: Path = ACTIVITY_CATALOG_PATH) -> None:
    """Append a validated activity atomically, refusing duplicate IDs."""

    added, duplicates = save_activities([activity], path)
    if duplicates:
        raise ValueError(f"{activity.name} is already in the curated catalog")


def update_activity(activity: Activity, path: Path = ACTIVITY_CATALOG_PATH) -> None:
    """Replace an existing validated activity while preserving its stable ID."""

    update_activities([activity], path=path)


def update_activities(
    activities_to_update: list[Activity],
    path: Path = ACTIVITY_CATALOG_PATH,
    *,
    sync_embeddings: bool = True,
) -> None:
    """Persist existing catalog records, optionally refreshing text embeddings once."""

    if not activities_to_update:
        return
    existing = load_curated_activities(path)
    existing_ids = {entry.id for entry in existing}
    missing_ids = [activity.id for activity in activities_to_update if activity.id not in existing_ids]
    if missing_ids:
        raise ValueError(f"{activities_to_update[0].name} is not in the curated catalog")
    if _uses_supabase(path):
        upsert_many(
            SUPABASE_CATALOG_TABLE,
            _remote_rows(activities_to_update),
            conflict="activity_id",
        )
        if sync_embeddings:
            _try_sync_embeddings(activities_to_update)
        return
    updates_by_id = {activity.id: activity for activity in activities_to_update}
    updated = [updates_by_id.get(entry.id, entry) for entry in existing]
    _write_activities(updated, path)


def auto_curate_candidates(
    candidates: list[dict[str, Any]],
    city: str,
    country: str,
) -> tuple[list[Activity], dict[str, str]]:
    """Turn safe imported candidates into validated batch-review activities.

    Excluded candidates are returned with a human-readable reason. Successful
    records have already passed the same Activity Pydantic contract as manual
    additions, but still retain their source URL for later review.
    """

    activities: list[Activity] = []
    skipped: dict[str, str] = {}
    for candidate in candidates:
        name = str(candidate.get("name", "Unnamed candidate"))
        reason = automatic_curation_reason(candidate)
        if reason:
            skipped[name] = reason
            continue
        fields = automatic_activity_fields(candidate, city)
        if fields is None:
            skipped[name] = "Auto-skip: no safe draft fields were available."
            continue
        try:
            activities.append(build_activity(candidate, city, country, fields))
        except (KeyError, ValueError) as error:
            skipped[name] = f"Auto-skip: {error}"
    return activities, skipped


def review_curate_candidates(
    candidates: list[dict[str, Any]],
    city: str,
    country: str,
) -> tuple[list[Activity], dict[str, str]]:
    """Create editable Pydantic-valid drafts for curator-approved edge cases."""

    activities: list[Activity] = []
    skipped: dict[str, str] = {}
    for candidate in candidates:
        name = str(candidate.get("name", "Unnamed candidate"))
        decision = classify_candidate(candidate)
        if decision.outcome != "review":
            skipped[name] = decision.reason
            continue
        # Review items use the same conservative defaults as automatic items,
        # but only after an explicit curator selection in the UI.
        from src.draft_curation import draft_activity
        draft = draft_activity(candidate)
        fields = {
            "name": name.strip(), "category": draft.category,
            "category_tags": list(draft.category_tags), "interests": list(draft.interests),
            "walking_level": draft.walking_level, "budget_level": draft.budget_level,
            "duration_hours": draft.duration_hours, "indoor": draft.indoor,
            "family_friendly": True, "reservation_required": draft.reservation_required,
            "accessibility_notes": "Verify current accessibility and visitor information before visiting.",
            "description": f"Visit {name}, a curated {draft.category.replace('_', ' ')} experience in {city}.",
            "source_url": candidate.get("source_url"),
            "official_url": candidate.get("official_url"),
            "wikipedia_url": candidate.get("wikipedia_url"),
        }
        try:
            activities.append(build_activity(candidate, city, country, fields))
        except (KeyError, ValueError) as error:
            skipped[name] = str(error)
    return activities, skipped


def save_activities(
    activities_to_add: list[Activity],
    path: Path = ACTIVITY_CATALOG_PATH,
) -> tuple[list[Activity], list[str]]:
    """Atomically add a validated batch, returning added and duplicate IDs."""

    existing = load_curated_activities(path)
    known_ids = {activity.id for activity in existing}
    added: list[Activity] = []
    duplicate_ids: list[str] = []
    for activity in activities_to_add:
        if activity.id in known_ids:
            duplicate_ids.append(activity.id)
            continue
        known_ids.add(activity.id)
        existing.append(activity)
        added.append(activity)
    if added:
        if _uses_supabase(path):
            upsert_many(SUPABASE_CATALOG_TABLE, _remote_rows(added), conflict="activity_id")
            _try_sync_embeddings(added)
        else:
            _write_activities(existing, path)
    return added, duplicate_ids


def delete_activities(
    activity_ids: list[str],
    path: Path = ACTIVITY_CATALOG_PATH,
) -> int:
    """Delete selected validated records from the local canonical catalog."""

    ids = set(activity_ids)
    if not ids:
        return 0
    activities = load_curated_activities(path)
    retained = [activity for activity in activities if activity.id not in ids]
    removed = len(activities) - len(retained)
    if removed:
        if _uses_supabase(path):
            delete_in(SUPABASE_CATALOG_TABLE, "activity_id", list(ids))
        else:
            _write_activities(retained, path)
    return removed


def delete_candidates_from_batch(path: Path, candidate_ids: list[str]) -> int:
    """Remove unwanted source candidates from one review batch atomically."""

    ids = set(candidate_ids)
    if not ids:
        return 0
    document = load_candidate_batch(path)
    candidates = document["candidates"]
    retained = [
        candidate for candidate in candidates
        if candidate.get("wikidata_id") not in ids
    ]
    removed = len(candidates) - len(retained)
    if not removed:
        return 0
    document["candidates"] = retained
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return removed


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
