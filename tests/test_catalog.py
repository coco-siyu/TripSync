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
)
from src.curation_seed import suggested_activities


CANDIDATE = {"name": "Uffizi Gallery", "source_url": "https://www.wikidata.org/wiki/Q51252"}
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
