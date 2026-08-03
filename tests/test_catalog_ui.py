"""Tests for defensive candidate-table selection."""

from __future__ import annotations

import json
import unittest
import tempfile
from pathlib import Path

from src.catalog import (
    auto_curate_candidates,
    delete_activities,
    delete_candidates_from_batch,
    load_candidate_batch,
    load_curated_activities,
    save_activities,
)


class CatalogUiTests(unittest.TestCase):
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

    def test_batch_curation_can_remove_selected_raw_candidates(self) -> None:
        """Removing a raw candidate leaves the remaining review batch intact."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "example_wikidata_candidates.json"
            path.write_text(
                json.dumps(
                    {
                        "city": "Example City",
                        "country": "Example Country",
                        "candidates": [
                            {"wikidata_id": "Q1", "name": "Keep me"},
                            {"wikidata_id": "Q2", "name": "Remove me"},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(delete_candidates_from_batch(path, ["Q2"]), 1)
            self.assertEqual(
                [candidate["wikidata_id"] for candidate in load_candidate_batch(path)["candidates"]],
                ["Q1"],
            )
