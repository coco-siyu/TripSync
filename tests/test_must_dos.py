"""Tests for must-do normalization, ownership, and suggestions."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.models import Activity, TravelerProfile
from src.must_dos import (
    canonicalize_activity_key,
    matches_must_do,
    resolve_must_dos,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DATA_PATH = REPOSITORY_ROOT / "data" / "sample_activities.json"


class MustDoResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        raw_activities = json.loads(
            SAMPLE_DATA_PATH.read_text(encoding="utf-8")
        )
        cls.activities = [
            Activity.model_validate(activity) for activity in raw_activities
        ]
        cls.vatican_museums = next(
            activity
            for activity in cls.activities
            if activity.id == "rome_vatican_museums"
        )

    def test_canonical_key_ignores_spacing_and_punctuation(self) -> None:
        expected = "vaticanmuseums"
        self.assertEqual(canonicalize_activity_key("Vatican Museums"), expected)
        self.assertEqual(canonicalize_activity_key("vatican_museums"), expected)
        self.assertEqual(canonicalize_activity_key("vatican-museums"), expected)

    def test_compact_text_matches_activity_name(self) -> None:
        self.assertTrue(
            matches_must_do(self.vatican_museums, "vaticanmuseums")
        )

    def test_resolution_combines_owners_without_duplicate_activity(self) -> None:
        travelers = [
            TravelerProfile(
                name="Coco",
                interests=["art"],
                walking_tolerance="moderate",
                must_do_activities=["vaticanmuseums"],
            ),
            TravelerProfile(
                name="Sam",
                interests=["history"],
                walking_tolerance="high",
                must_do_activities=["Vatican-Museums"],
            ),
        ]

        resolution = resolve_must_dos(self.activities, travelers)

        self.assertEqual(
            resolution.owners_by_activity_id,
            {"rome_vatican_museums": ("Coco", "Sam")},
        )
        self.assertFalse(resolution.unmatched)

    def test_unmatched_entry_offers_close_activity_suggestion(self) -> None:
        traveler = TravelerProfile(
            name="Coco",
            interests=["art"],
            walking_tolerance="moderate",
            must_do_activities=["vatican museumz"],
        )

        resolution = resolve_must_dos(self.activities, [traveler])

        self.assertFalse(resolution.owners_by_activity_id)
        self.assertEqual(len(resolution.unmatched), 1)
        self.assertEqual(
            resolution.unmatched[0].suggested_activity_name,
            "Vatican Museums",
        )


if __name__ == "__main__":
    unittest.main()
