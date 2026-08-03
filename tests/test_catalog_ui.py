"""Tests for defensive candidate-table selection."""

from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from src.catalog import auto_curate_candidates, delete_activities, load_curated_activities, save_activities
from src.catalog_ui import _selected_candidate


class CatalogUiTests(unittest.TestCase):
    def test_selected_candidate_rejects_stale_or_missing_selection(self) -> None:
        rows = [{"candidate": {"name": "One"}}, {"candidate": {"name": "Two"}}]

        self.assertIsNone(_selected_candidate(rows, []))
        self.assertIsNone(_selected_candidate(rows, [2]))
        self.assertIsNone(_selected_candidate(rows, [0, 1]))
        self.assertEqual(_selected_candidate(rows, [1]), {"name": "Two"})

    def test_batch_curation_skips_hotels_and_publishes_valid_activities(self) -> None:
        candidates = [
            {
                "name": "Example Museum",
                "source_url": "https://www.wikidata.org/wiki/Q1",
                "wikidata_types": ["museum"],
            },
            {
                "name": "Example Hotel",
                "source_url": "https://www.wikidata.org/wiki/Q2",
                "wikidata_types": ["hotel"],
            },
        ]

        activities, skipped = auto_curate_candidates(candidates, "Example City", "Example Country")

        self.assertEqual([activity.name for activity in activities], ["Example Museum"])
        self.assertIn("Example Hotel", skipped)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "activities.json"
            added, duplicates = save_activities(activities, path)
            self.assertEqual(len(added), 1)
            self.assertEqual(duplicates, [])
            self.assertEqual(len(load_curated_activities(path)), 1)
            self.assertEqual(delete_activities([added[0].id], path), 1)
            self.assertEqual(load_curated_activities(path), [])
