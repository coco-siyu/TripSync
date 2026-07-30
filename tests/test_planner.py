"""Tests for deterministic shortlist-to-itinerary scheduling."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from src.models import (
    Activity,
    ItineraryPlan,
    ItinerarySource,
    TravelerProfile,
    TripRequest,
)
from src.planner import PACE_RULES, build_itinerary
from src.scoring import rank_activities


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DATA_PATH = REPOSITORY_ROOT / "data" / "sample_activities.json"


def make_trip(*, days: int = 3, pace: str = "balanced") -> TripRequest:
    return TripRequest(
        destination="Rome",
        country="Italy",
        days=days,
        budget_level="moderate",
        pace=pace,
        travelers=[
            TravelerProfile(
                name="Coco",
                interests=["history", "food", "photography"],
                walking_tolerance="moderate",
                must_do_activities=["Colosseum"],
            ),
            TravelerProfile(
                name="Sam",
                interests=["art", "architecture", "relaxation"],
                walking_tolerance="low",
                must_do_activities=["Borghese Gallery"],
            ),
        ],
    )


class ItineraryPlannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        raw_activities = json.loads(
            SAMPLE_DATA_PATH.read_text(encoding="utf-8")
        )
        cls.activities = [
            Activity.model_validate(activity) for activity in raw_activities
        ]

    def setUp(self) -> None:
        self.trip = make_trip()
        self.results = rank_activities(self.activities, self.trip)
        self.must_do_owners = {
            "rome_colosseum": ("Coco",),
            "rome_borghese_gallery": ("Sam",),
        }

    def test_plan_has_requested_days_within_pace_limits(self) -> None:
        plan = build_itinerary(
            self.trip,
            self.activities,
            self.results,
            ["rome_colosseum", "rome_borghese_gallery"],
            must_do_owners_by_activity_id=self.must_do_owners,
        )
        rule = PACE_RULES[self.trip.pace]

        self.assertEqual(len(plan.days), 3)
        self.assertEqual(
            [day.day_number for day in plan.days],
            [1, 2, 3],
        )
        self.assertTrue(
            all(day.planned_hours <= rule.capacity_hours for day in plan.days)
        )
        self.assertTrue(
            all(len(day.activities) <= rule.max_activities for day in plan.days)
        )

    def test_shortlisted_must_do_is_prioritized(self) -> None:
        plan = build_itinerary(
            self.trip,
            self.activities,
            self.results,
            ["rome_pantheon", "rome_colosseum"],
            must_do_owners_by_activity_id=self.must_do_owners,
            auto_fill=False,
        )

        first_activity = plan.days[0].activities[0]
        self.assertEqual(first_activity.activity_id, "rome_colosseum")
        self.assertEqual(first_activity.must_do_owners, ["Coco"])
        self.assertIn("Must-do for Coco", first_activity.reason)

    def test_auto_fill_false_schedules_only_unique_shortlist_items(self) -> None:
        plan = build_itinerary(
            self.trip,
            self.activities,
            self.results,
            [
                "rome_pantheon",
                "rome_pantheon",
                "rome_trevi_fountain",
            ],
            auto_fill=False,
        )
        scheduled_ids = [
            activity.activity_id
            for day in plan.days
            for activity in day.activities
        ]

        self.assertCountEqual(
            scheduled_ids,
            ["rome_pantheon", "rome_trevi_fountain"],
        )
        self.assertTrue(
            all(
                activity.source == ItinerarySource.SHORTLIST
                for day in plan.days
                for activity in day.activities
            )
        )

    def test_auto_fill_uses_group_fit_ranking_without_duplicates(self) -> None:
        plan = build_itinerary(
            self.trip,
            self.activities,
            self.results,
            ["rome_colosseum"],
            must_do_owners_by_activity_id=self.must_do_owners,
            auto_fill=True,
        )
        scheduled = [
            activity
            for day in plan.days
            for activity in day.activities
        ]
        scheduled_ids = [activity.activity_id for activity in scheduled]

        self.assertGreater(len(scheduled), 1)
        self.assertEqual(len(scheduled_ids), len(set(scheduled_ids)))
        self.assertTrue(
            any(
                activity.source == ItinerarySource.RECOMMENDATION
                for activity in scheduled
            )
        )

    def test_selected_activity_too_long_for_pace_is_explained(self) -> None:
        long_activity = self.activities[0].model_copy(
            update={
                "id": "rome_full_day_tour",
                "name": "Full Day Tour",
                "duration_hours": 6.0,
            }
        )
        trip = make_trip(days=1, pace="relaxed")
        results = rank_activities([long_activity], trip)

        plan = build_itinerary(
            trip,
            [long_activity],
            results,
            [long_activity.id],
            auto_fill=False,
        )

        self.assertFalse(plan.days[0].activities)
        self.assertEqual(len(plan.unscheduled), 1)
        self.assertIn("longer than", plan.unscheduled[0].reason)

    def test_unknown_shortlist_activity_is_not_invented(self) -> None:
        plan = build_itinerary(
            self.trip,
            self.activities,
            self.results,
            ["rome_missing_activity"],
            auto_fill=False,
        )

        self.assertFalse(
            any(day.activities for day in plan.days)
        )
        self.assertEqual(
            plan.unscheduled[0].activity_id,
            "rome_missing_activity",
        )
        self.assertIn("retrieved activity catalog", plan.unscheduled[0].reason)

    def test_planner_output_is_deterministic(self) -> None:
        arguments = (
            self.trip,
            self.activities,
            self.results,
            ["rome_colosseum", "rome_borghese_gallery"],
        )
        first = build_itinerary(
            *arguments,
            must_do_owners_by_activity_id=self.must_do_owners,
        )
        second = build_itinerary(
            *arguments,
            must_do_owners_by_activity_id=self.must_do_owners,
        )

        self.assertEqual(
            first.model_dump(mode="json"),
            second.model_dump(mode="json"),
        )

    def test_plan_model_rejects_duplicate_activity_across_days(self) -> None:
        plan = build_itinerary(
            self.trip,
            self.activities,
            self.results,
            ["rome_colosseum", "rome_borghese_gallery"],
            must_do_owners_by_activity_id=self.must_do_owners,
            auto_fill=False,
        )
        payload = plan.model_dump(mode="json")
        duplicate = payload["days"][0]["activities"][0]
        payload["days"][1]["activities"].append(duplicate)
        payload["days"][1]["activity_hours"] += duplicate["duration_hours"]
        payload["days"][1]["transition_hours"] += 0.5
        payload["days"][1]["planned_hours"] = (
            payload["days"][1]["activity_hours"]
            + payload["days"][1]["transition_hours"]
        )

        with self.assertRaisesRegex(
            ValidationError,
            "activity may only be scheduled once",
        ):
            ItineraryPlan.model_validate(payload)

    def test_day_model_rejects_time_above_capacity(self) -> None:
        plan = build_itinerary(
            self.trip,
            self.activities,
            self.results,
            ["rome_colosseum"],
            must_do_owners_by_activity_id=self.must_do_owners,
            auto_fill=False,
        )
        payload = plan.model_dump(mode="json")
        payload["days"][0]["capacity_hours"] = 1

        with self.assertRaisesRegex(
            ValidationError,
            "planned hours exceed daily capacity",
        ):
            ItineraryPlan.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
