"""Tests for deterministic group-fit scoring."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.models import Activity, TravelerProfile, TripRequest
from src.scoring import rank_activities, score_activity


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DATA_PATH = REPOSITORY_ROOT / "data" / "sample_activities.json"


def make_traveler(
    name: str,
    interests: list[str],
    walking_tolerance: str = "moderate",
    must_do_activities: list[str] | None = None,
) -> TravelerProfile:
    return TravelerProfile(
        name=name,
        interests=interests,
        walking_tolerance=walking_tolerance,
        must_do_activities=must_do_activities or [],
    )


def make_trip(
    travelers: list[TravelerProfile],
    budget_level: str = "moderate",
) -> TripRequest:
    return TripRequest(
        destination="Rome",
        country="Italy",
        days=3,
        budget_level=budget_level,
        pace="balanced",
        travelers=travelers,
    )


def make_activity(
    activity_id: str,
    name: str,
    interests: list[str],
    walking_level: str = "low",
    budget_level: str = "moderate",
) -> Activity:
    return Activity(
        id=activity_id,
        name=name,
        city="Rome",
        country="Italy",
        category="museum",
        interests=interests,
        walking_level=walking_level,
        budget_level=budget_level,
        duration_hours=2,
        indoor=True,
        family_friendly=True,
        accessibility_notes="Step-free entrance is available.",
        reservation_required=False,
        description="A deterministic activity fixture.",
        source_url="https://example.com/activity",
    )


class GroupFitScoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.group = make_trip(
            [
                make_traveler("Coco", ["art", "history"]),
                make_traveler("Sam", ["architecture", "history"]),
            ]
        )

    def test_interest_match_scores_above_irrelevant_activity(self) -> None:
        relevant = make_activity(
            "rome_history_museum",
            "History Museum",
            ["history", "architecture"],
        )
        irrelevant = make_activity(
            "rome_sports_museum",
            "Sports Museum",
            ["sports"],
        )

        self.assertGreater(
            score_activity(relevant, self.group).total_score,
            score_activity(irrelevant, self.group).total_score,
        )

    def test_semantic_interest_can_support_a_typed_preference(self) -> None:
        activity = make_activity("rome_gallery", "Gallery", ["art"])
        trip = make_trip(
            [
                make_traveler("Coco", ["painting"]),
                make_traveler("Sam", ["food"]),
            ]
        )

        coco_fit = score_activity(
            activity,
            trip,
            semantic_similarities={"Coco": 0.60},
        ).traveler_fits[0]

        self.assertGreater(coco_fit.semantic_interest_score, 0)
        self.assertGreater(coco_fit.score, 25)
        self.assertIn("Semantic alignment", coco_fit.explanations[0])

    def test_must_do_match_adds_twenty_points_for_traveler(self) -> None:
        activity = make_activity(
            "rome_colosseum",
            "Colosseum",
            ["ancient rome"],
        )
        without_must_do = make_trip(
            [
                make_traveler("Coco", ["food"]),
                make_traveler("Sam", ["architecture"]),
            ]
        )
        with_must_do = make_trip(
            [
                make_traveler("Coco", ["food"], must_do_activities=["Colosseum"]),
                make_traveler("Sam", ["architecture"]),
            ]
        )

        base_fit = score_activity(activity, without_must_do).traveler_fits[0]
        boosted_fit = score_activity(activity, with_must_do).traveler_fits[0]

        self.assertFalse(base_fit.must_do_match)
        self.assertTrue(boosted_fit.must_do_match)
        self.assertEqual(boosted_fit.score - base_fit.score, 20)

    def test_must_do_match_ignores_spacing_and_punctuation(self) -> None:
        activity = make_activity(
            "rome_vatican_museums",
            "Vatican Museums",
            ["art"],
        )
        trip = make_trip(
            [
                make_traveler(
                    "Coco",
                    ["food"],
                    must_do_activities=["vaticanmuseums"],
                ),
                make_traveler("Sam", ["architecture"]),
            ]
        )

        coco_fit = score_activity(activity, trip).traveler_fits[0]

        self.assertTrue(coco_fit.must_do_match)
        self.assertEqual(coco_fit.must_do_score, 20)

    def test_walking_conflict_is_penalized_and_explained(self) -> None:
        activity = make_activity(
            "rome_hill_walk",
            "Hill Walk",
            ["history"],
            walking_level="high",
        )
        trip = make_trip(
            [
                make_traveler("Coco", ["history"], walking_tolerance="low"),
                make_traveler("Sam", ["history"], walking_tolerance="high"),
            ]
        )

        result = score_activity(activity, trip)
        coco_fit = result.traveler_fits[0]

        self.assertFalse(coco_fit.walking_compatible)
        self.assertEqual(coco_fit.walking_score, 0)
        self.assertTrue(any("Coco" in tradeoff for tradeoff in result.tradeoffs))

    def test_activity_above_budget_receives_budget_penalty(self) -> None:
        low_cost = make_activity(
            "rome_free_museum",
            "Free Museum",
            ["history"],
            budget_level="free",
        )
        expensive = make_activity(
            "rome_premium_museum",
            "Premium Museum",
            ["history"],
            budget_level="high",
        )
        low_budget_trip = make_trip(self.group.travelers, budget_level="low")

        low_cost_result = score_activity(low_cost, low_budget_trip)
        expensive_result = score_activity(expensive, low_budget_trip)

        self.assertEqual(low_cost_result.budget_score, 100)
        self.assertEqual(expensive_result.budget_score, 0)
        self.assertGreater(low_cost_result.total_score, expensive_result.total_score)

    def test_fairness_rewards_activity_serving_both_travelers(self) -> None:
        balanced = make_activity(
            "rome_balanced_museum",
            "Balanced Museum",
            ["art", "architecture"],
        )
        one_sided = make_activity(
            "rome_art_museum",
            "Art Museum",
            ["art", "painting"],
        )

        balanced_result = score_activity(balanced, self.group)
        one_sided_result = score_activity(one_sided, self.group)

        self.assertGreater(
            balanced_result.coverage_count,
            one_sided_result.coverage_count,
        )
        self.assertGreater(
            balanced_result.fairness_score,
            one_sided_result.fairness_score,
        )
        self.assertGreater(
            balanced_result.total_score,
            one_sided_result.total_score,
        )

    def test_destination_mismatch_is_rejected(self) -> None:
        activity = make_activity(
            "paris_art_museum",
            "Paris Art Museum",
            ["art"],
        ).model_copy(update={"city": "Paris", "country": "France"})

        with self.assertRaisesRegex(ValueError, "does not match"):
            score_activity(activity, self.group)

    def test_sample_catalog_ranks_all_activities_deterministically(self) -> None:
        raw_activities = json.loads(SAMPLE_DATA_PATH.read_text(encoding="utf-8"))
        activities = [Activity.model_validate(item) for item in raw_activities]

        first_ranking = rank_activities(activities, self.group)
        second_ranking = rank_activities(activities, self.group)

        self.assertEqual(len(first_ranking), len(activities))
        self.assertEqual(
            [result.activity_id for result in first_ranking],
            [result.activity_id for result in second_ranking],
        )
        self.assertEqual(
            [result.total_score for result in first_ranking],
            sorted(
                [result.total_score for result in first_ranking],
                reverse=True,
            ),
        )


if __name__ == "__main__":
    unittest.main()
