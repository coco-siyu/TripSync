"""Tests for deterministic advisory itinerary budgets."""

from __future__ import annotations

import unittest

from src.budget import activity_cost_range, estimate_daily_budget, euro_range
from src.catalog import load_curated_activities
from src.models import ItinerarySource, ScheduledActivity


class BudgetEstimateTests(unittest.TestCase):
    def test_catalog_budget_level_maps_to_a_stable_range(self) -> None:
        free_activity = next(
            activity
            for activity in load_curated_activities()
            if activity.budget_level.value == "free"
        )

        self.assertEqual(activity_cost_range(free_activity), (0, 0))

    def test_daily_estimate_includes_activities_and_food(self) -> None:
        activities = [
            ScheduledActivity(
                activity_id="rome_one",
                activity_name="Activity one",
                duration_hours=1,
                estimated_cost_min_eur=20,
                estimated_cost_max_eur=40,
                source=ItinerarySource.RECOMMENDATION,
                reason="A grounded activity.",
            ),
            ScheduledActivity(
                activity_id="rome_two",
                activity_name="Activity two",
                duration_hours=1,
                estimated_cost_min_eur=40,
                estimated_cost_max_eur=80,
                source=ItinerarySource.RECOMMENDATION,
                reason="A grounded activity.",
            ),
        ]

        estimate = estimate_daily_budget(activities, "low")

        self.assertIsNotNone(estimate)
        assert estimate is not None
        self.assertEqual((estimate.activity_min_eur, estimate.activity_max_eur), (60, 120))
        self.assertEqual((estimate.food_min_eur, estimate.food_max_eur), (30, 50))
        self.assertEqual((estimate.total_min_eur, estimate.total_max_eur), (90, 170))
        self.assertTrue(estimate.potentially_over_budget)

    def test_legacy_activity_without_costs_has_no_misleading_estimate(self) -> None:
        legacy = ScheduledActivity(
            activity_id="rome_legacy",
            activity_name="Legacy activity",
            duration_hours=1,
            source=ItinerarySource.SHORTLIST,
            reason="Loaded from an older saved plan.",
        )

        self.assertIsNone(estimate_daily_budget([legacy], "moderate"))

    def test_formats_single_values_and_ranges(self) -> None:
        self.assertEqual(euro_range(20, 20), "€20")
        self.assertEqual(euro_range(50, 100), "€50–€100")


if __name__ == "__main__":
    unittest.main()
