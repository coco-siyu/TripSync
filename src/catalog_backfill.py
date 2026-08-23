"""Backfill optional route coordinates for existing curated activities.

Run this after adding coordinate support to an established catalog. It updates
only records that have a Wikidata source URL and are missing both coordinate
fields; curated descriptions, tags, and activity IDs stay untouched.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urlparse

from src.catalog import ACTIVITY_CATALOG_PATH, load_curated_activities, update_activities
from src.catalog_import import (
    CatalogImportError,
    WIKIDATA_ENTITY_URL,
    fetch_coordinates,
    fetch_official_url_candidates,
)
from src.models import Activity
from src.official_sites import (
    OfficialSiteCandidate,
    OfficialSiteDetails,
    verify_official_sites,
)
from src.osm_enrichment import OsmDetails, enrich_activity, fetch_osm_details_many


CoordinateFetcher = Callable[[list[str]], dict[str, tuple[float, float]]]
OsmFetcher = Callable[[Activity], OsmDetails]
OsmBatchFetcher = Callable[[list[Activity]], dict[str, OsmDetails]]
OfficialLocatorFetcher = Callable[[list[str]], dict[str, str]]
OfficialBatchVerifier = Callable[
    [list[OfficialSiteCandidate]], dict[str, OfficialSiteDetails]
]


def wikidata_id_from_source(activity: Activity) -> str | None:
    """Extract a stable Wikidata item ID from an activity's provenance URL."""

    if activity.wikidata_id:
        return activity.wikidata_id
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


def _direct_official_source(activity: Activity) -> str | None:
    """Recover a curated official URL from older primary-source records."""

    source_url = str(activity.source_url)
    hostname = (urlparse(source_url).hostname or "").casefold()
    reference_hosts = (
        "wikidata.org",
        "wikipedia.org",
        "openstreetmap.org",
    )
    if any(
        hostname == host or hostname.endswith(f".{host}")
        for host in reference_hosts
    ):
        return None
    return source_url


def backfill_official_sites(
    activities: list[Activity],
    *,
    locator_fetcher: OfficialLocatorFetcher = fetch_official_url_candidates,
    batch_verifier: OfficialBatchVerifier = verify_official_sites,
    include_verified: bool = False,
) -> tuple[list[Activity], int, int]:
    """Validate real official sites and add direct visitor-information links."""

    targets = (
        activities
        if include_verified
        else [
            activity
            for activity in activities
            if not activity.official_site_verified
        ]
    )
    ids_by_activity = {
        activity.id: wikidata_id_from_source(activity) for activity in targets
    }
    locator_ids = sorted(
        {
            item_id
            for activity in targets
            if not activity.official_url and not _direct_official_source(activity)
            if (item_id := ids_by_activity[activity.id])
        }
    )
    locator_urls: dict[str, str] = {}
    for start in range(0, len(locator_ids), 100):
        locator_urls.update(locator_fetcher(locator_ids[start : start + 100]))

    site_candidates: list[OfficialSiteCandidate] = []
    for activity in targets:
        item_id = ids_by_activity[activity.id]
        candidate_url = (
            str(activity.official_url)
            if activity.official_url
            else _direct_official_source(activity)
            or (locator_urls.get(item_id) if item_id else None)
        )
        if candidate_url:
            site_candidates.append(
                OfficialSiteCandidate(
                    activity.id,
                    activity.name,
                    candidate_url,
                )
            )
    verified = batch_verifier(site_candidates)
    checked_at = datetime.now(UTC)
    updated: list[Activity] = []
    for activity in targets:
        details = verified.get(activity.id)
        if details is None:
            continue
        updates = {
            "official_url": details.official_url,
            "official_site_verified": True,
            "official_visit_url": details.visit_url,
            "official_hours_url": details.hours_url,
            "official_tickets_url": details.tickets_url,
            "official_site_checked_at": checked_at,
        }
        item_id = ids_by_activity[activity.id]
        if item_id and not activity.wikidata_id:
            updates["wikidata_id"] = item_id
        updated.append(Activity.model_validate({**dict(activity), **updates}))
    return updated, len(updated), len(targets) - len(updated)


def backfill_osm_details(
    activities: list[Activity], *, fetcher: OsmFetcher | None = None,
    batch_fetcher: OsmBatchFetcher = fetch_osm_details_many,
) -> tuple[list[Activity], int, int]:
    """Add missing OSM details, preserving curator fields and batching live calls."""

    updated: list[Activity] = []
    refreshed = 0
    unresolved = 0
    missing = [
        activity
        for activity in activities
        if not (activity.address and activity.opening_hours and activity.osm_url)
    ]

    if fetcher is not None:
        # Keep this narrow injectable path for deterministic unit tests.
        details_by_activity: dict[str, OsmDetails] = {}
        for activity in missing:
            try:
                details_by_activity[activity.id] = fetcher(activity)
            except CatalogImportError:
                unresolved += 1
    else:
        try:
            details_by_activity = batch_fetcher(missing)
        except CatalogImportError:
            raise

    for activity in missing:
        details = details_by_activity.get(activity.id)
        if details is None:
            unresolved += 1
            continue
        enriched = enrich_activity(activity, details)
        if enriched is None:
            unresolved += 1
            continue
        updated.append(enriched)
        refreshed += 1
    return updated, refreshed, unresolved


def main() -> None:
    """Update the configured shared catalog without overwriting curated fields."""

    parser = argparse.ArgumentParser(description="Backfill TripSync route coordinates and optional OSM visit details.")
    parser.add_argument("--dry-run", action="store_true", help="Report possible updates without saving them.")
    detail_group = parser.add_mutually_exclusive_group()
    detail_group.add_argument(
        "--osm-details", action="store_true",
        help="Add missing supplementary OpenStreetMap details.",
    )
    detail_group.add_argument(
        "--official-sites", action="store_true",
        help="Validate actual official sites and discover visit, hours, and ticket links.",
    )
    parser.add_argument(
        "--refresh-verified", action="store_true",
        help="With --official-sites, recheck previously verified sites as well as missing ones.",
    )
    arguments = parser.parse_args()
    if arguments.refresh_verified and not arguments.official_sites:
        parser.error("--refresh-verified requires --official-sites")

    activities = load_curated_activities(ACTIVITY_CATALOG_PATH)
    try:
        if arguments.official_sites:
            updated, refreshed, unresolved = backfill_official_sites(
                activities,
                include_verified=arguments.refresh_verified,
            )
            detail_label = "verified official-site links"
        elif arguments.osm_details:
            updated, refreshed, unresolved = backfill_osm_details(activities)
            detail_label = "OpenStreetMap visit details"
        else:
            updated, refreshed, unresolved = backfill_coordinates(activities)
            detail_label = "coordinates"
    except CatalogImportError as error:
        parser.exit(1, f"Catalog refresh could not finish: {error}\n")

    if arguments.dry_run:
        print(f"Would add {detail_label} to {refreshed} activity record(s); {unresolved} could not be resolved.")
        return
    # Coordinates do not contribute to the stable text used for semantic
    # embeddings, so this is one catalog write with no unnecessary embedding
    # lookup or OpenAI call per activity.
    update_activities(updated, ACTIVITY_CATALOG_PATH, sync_embeddings=False)
    print(f"Added {detail_label} to {refreshed} activity record(s); {unresolved} could not be resolved.")


if __name__ == "__main__":
    main()
