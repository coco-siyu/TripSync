"""Tests for TripSync's validated input and activity models."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from src.models import (
    Activity,
    BudgetLevel,
    TravelerProfile,
    TripPace,
    TripRequest,
    WalkingLevel,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DATA_PATH = REPOSITORY_ROOT / "data" / "sample_activities.json"


def traveler(name: str) -> TravelerProfile:
    return TravelerProfile(
        name=name,
        interests=["History", " Food ", "history"],
        walking_tolerance=WalkingLevel.MODERATE,
    )


class TravelerProfileTests(unittest.TestCase):
    def test_normalizes_and_deduplicates_tags(self) -> None:
        profile = TravelerProfile(
            name="Coco",
            interests=[" History ", "FOOD", "history"],
            walking_tolerance="moderate",
            food_restrictions=[" Vegetarian ", "vegetarian"],
        )

        self.assertEqual(profile.interests, ["history", "food"])
        self.assertEqual(profile.food_restrictions, ["vegetarian"])

    def test_requires_at_least_one_interest(self) -> None:
        with self.assertRaises(ValidationError):
            TravelerProfile(
                name="Coco",
                interests=[],
                walking_tolerance="low",
            )


class TripRequestTests(unittest.TestCase):
    def test_accepts_two_to_six_unique_travelers(self) -> None:
        request = TripRequest(
            destination="Rome",
            country="Italy",
            days=3,
            budget_level=BudgetLevel.MODERATE,
            pace=TripPace.BALANCED,
            travelers=[traveler("Coco"), traveler("Sam")],
        )

        self.assertEqual(request.days, 3)
        self.assertEqual(len(request.travelers), 2)

    def test_rejects_duplicate_traveler_names_case_insensitively(self) -> None:
        with self.assertRaisesRegex(ValidationError, "traveler names must be unique"):
            TripRequest(
                destination="Rome",
                country="Italy",
                days=3,
                budget_level="moderate",
                pace="balanced",
                travelers=[traveler("Coco"), traveler("coco")],
            )

    def test_rejects_trips_longer_than_mvp_scope(self) -> None:
        with self.assertRaises(ValidationError):
            TripRequest(
                destination="Rome",
                country="Italy",
                days=6,
                budget_level="moderate",
                pace="balanced",
                travelers=[traveler("Coco"), traveler("Sam")],
            )


class ActivityTests(unittest.TestCase):
    def test_sample_catalog_is_valid_and_has_unique_ids(self) -> None:
        raw_activities = json.loads(SAMPLE_DATA_PATH.read_text(encoding="utf-8"))
        activities = [Activity.model_validate(item) for item in raw_activities]

        ids = [activity.id for activity in activities]
        self.assertGreaterEqual(len(activities), 10)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(
            len({activity.category for activity in activities}),
            6,
        )

    def test_rejects_unknown_activity_fields(self) -> None:
        raw_activity = json.loads(SAMPLE_DATA_PATH.read_text(encoding="utf-8"))[0]
        raw_activity["live_price"] = 25

        with self.assertRaises(ValidationError):
            Activity.model_validate(raw_activity)

    def test_rejects_non_positive_duration(self) -> None:
        raw_activity = json.loads(SAMPLE_DATA_PATH.read_text(encoding="utf-8"))[0]
        raw_activity["duration_hours"] = 0

        with self.assertRaises(ValidationError):
            Activity.model_validate(raw_activity)


if __name__ == "__main__":
    unittest.main()
