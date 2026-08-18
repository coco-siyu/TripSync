"""Backfill optional route coordinates for existing curated activities.

Run this after adding coordinate support to an established catalog. It updates
only records that have a Wikidata source URL and are missing both coordinate
fields; curated descriptions, tags, and activity IDs stay untouched.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable

from src.catalog import ACTIVITY_CATALOG_PATH, load_curated_activities, update_activity
from src.catalog_import import CatalogImportError, WIKIDATA_ENTITY_URL, fetch_coordinates
from src.models import Activity


CoordinateFetcher = Callable[[list[str]], dict[str, tuple[float, float]]]


def wikidata_id_from_source(activity: Activity) -> str | None:
    """Extract a stable Wikidata item ID from an activity's provenance URL."""

    source = str(activity.source_url)
    if not source.startswith(WIKIDATA_ENTITY_URL):
        return None
    item_id = source.removeprefix(WIKIDATA_ENTITY_URL).split("?", 1)[0].strip()
    if item_id.startswith("Q") and item_id[1:].isdigit():
        return item_id
    return None


def backfill_coordinates(
    activities: list[Activity], *, fetcher: CoordinateFetcher = fetch_coordinates
) -> tuple[list[Activity], int, int]:
    """Return updated copies plus counts for refreshed and unresolved records."""

    missing = [
        activity
        for activity in activities
        if activity.latitude is None and activity.longitude is None
    ]
    ids_by_activity = {
        activity.id: wikidata_id_from_source(activity) for activity in missing
    }
    known_ids = sorted({item_id for item_id in ids_by_activity.values() if item_id})
    coordinates: dict[str, tuple[float, float]] = {}
    for start in range(0, len(known_ids), 100):
        coordinates.update(fetcher(known_ids[start : start + 100]))

    updated: list[Activity] = []
    refreshed = 0
    unresolved = 0
    for activity in missing:
        item_id = ids_by_activity[activity.id]
        location = coordinates.get(item_id) if item_id else None
        if location is None:
            unresolved += 1
            continue
        latitude, longitude = location
        updated.append(activity.model_copy(update={"latitude": latitude, "longitude": longitude}))
        refreshed += 1
    return updated, refreshed, unresolved


def main() -> None:
    """Update the configured shared catalog without overwriting curated fields."""

    parser = argparse.ArgumentParser(description="Backfill TripSync route coordinates from Wikidata.")
    parser.add_argument("--dry-run", action="store_true", help="Report possible updates without saving them.")
    arguments = parser.parse_args()

    activities = load_curated_activities(ACTIVITY_CATALOG_PATH)
    try:
        updated, refreshed, unresolved = backfill_coordinates(activities)
    except CatalogImportError as error:
        parser.exit(1, f"Coordinate refresh could not finish: {error}\n")

    if arguments.dry_run:
        print(f"Would add coordinates to {refreshed} activity record(s); {unresolved} could not be resolved.")
        return
    for activity in updated:
        update_activity(activity, ACTIVITY_CATALOG_PATH)
    print(f"Added coordinates to {refreshed} activity record(s); {unresolved} could not be resolved.")


if __name__ == "__main__":
    main()
