"""Tests for Pydantic-backed activity promotion."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from src.catalog import (
    activity_id,
    build_activity,
    consolidate_sample_activities,
    load_curated_activities,
    save_activity,
    update_activities,
)
from src.catalog_backfill import backfill_coordinates, wikidata_id_from_source
from src.curation_seed import suggested_activities


CANDIDATE = {
    "name": "Uffizi Gallery",
    "source_url": "https://www.wikidata.org/wiki/Q51252",
    "latitude": 43.7696,
    "longitude": 11.2558,
    "wikidata_id": "Q51252",
    "official_url": "https://www.uffizi.it/",
    "wikipedia_url": "https://en.wikipedia.org/wiki/Uffizi",
}
FIELDS = {
    "name": "Uffizi Gallery", "category": "museum", "interests": ["art", "history"],
    "walking_level": "moderate", "budget_level": "moderate", "duration_hours": 3,
    "indoor": True, "family_friendly": True, "reservation_required": True,
    "accessibility_notes": "Check official visitor information.",
    "description": "A major art museum in Florence.", "source_url": CANDIDATE["source_url"],
}


class CatalogTests(unittest.TestCase):
    def test_creates_stable_activity_id(self) -> None:
        self.assertEqual(activity_id("Florence", "Uffizi Gallery"), "florence_uffizi_gallery")

    def test_validates_and_persists_a_curated_activity(self) -> None:
        activity = build_activity(CANDIDATE, "Florence", "Italy", FIELDS)
        self.assertEqual(activity.latitude, 43.7696)
        self.assertEqual(activity.longitude, 11.2558)
        self.assertEqual(activity.wikidata_id, "Q51252")
        self.assertEqual(str(activity.official_url), "https://www.uffizi.it/")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "activities.json"
            save_activity(activity, path)
            self.assertEqual(load_curated_activities(path), [activity])
            with self.assertRaisesRegex(ValueError, "already"):
                save_activity(activity, path)

    def test_rejects_incomplete_planning_details(self) -> None:
        invalid = {**FIELDS, "interests": []}
        with self.assertRaises(ValidationError):
            build_activity(CANDIDATE, "Florence", "Italy", invalid)

    def test_suggested_catalog_is_fully_valid_and_unique(self) -> None:
        activities = suggested_activities()
        self.assertEqual(len(activities), len({activity.id for activity in activities}))
        self.assertGreaterEqual(len(activities), 20)

    def test_consolidates_legacy_sample_into_one_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "activities.json"
            count = consolidate_sample_activities(path)
            self.assertEqual(count, 12)
            self.assertEqual(len(load_curated_activities(path)), 12)

    def test_backfills_only_missing_coordinates_from_wikidata_provenance(self) -> None:
        missing_coordinates = build_activity(
            {"name": "Uffizi Gallery", "source_url": CANDIDATE["source_url"]},
            "Florence", "Italy", FIELDS,
        )
        existing_coordinates = missing_coordinates.model_copy(
            update={"latitude": 43.7696, "longitude": 11.2558}
        )

        updated, refreshed, unresolved = backfill_coordinates(
            [missing_coordinates, existing_coordinates],
            fetcher=lambda ids: {"Q51252": (43.7696, 11.2558)},
        )

        self.assertEqual(refreshed, 1)
        self.assertEqual(unresolved, 0)
        self.assertEqual(updated[0].latitude, 43.7696)
        self.assertEqual(updated[0].longitude, 11.2558)
        self.assertEqual(wikidata_id_from_source(updated[0]), "Q51252")

    def test_backfill_uses_stored_wikidata_id_when_primary_source_changes(self) -> None:
        activity = build_activity(CANDIDATE, "Florence", "Italy", FIELDS).model_copy(
            update={"source_url": "https://www.uffizi.it/", "latitude": None, "longitude": None}
        )
        self.assertEqual(wikidata_id_from_source(activity), "Q51252")

    def test_updates_multiple_existing_records_in_one_local_write(self) -> None:
        first = build_activity(CANDIDATE, "Florence", "Italy", FIELDS)
        second = first.model_copy(
            update={"id": "florence_bargello", "name": "Bargello National Museum"}
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "activities.json"
            save_activity(first, path)
            save_activity(second, path)
            update_activities(
                [
                    first.model_copy(update={"latitude": 43.77, "longitude": 11.26}),
                    second.model_copy(update={"latitude": 43.771, "longitude": 11.257}),
                ],
                path,
                sync_embeddings=False,
            )
            saved = {activity.id: activity for activity in load_curated_activities(path)}
            self.assertEqual(saved[first.id].latitude, 43.77)
            self.assertEqual(saved[second.id].longitude, 11.257)
