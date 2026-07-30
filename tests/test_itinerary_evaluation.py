"""Tests for the deterministic itinerary-quality benchmark."""

from __future__ import annotations

import unittest

from evaluation.itinerary import (
    coverage_fairness,
    evaluate_itineraries,
    load_itinerary_cases,
)
from evaluation.retrieval import load_activities


class ItineraryMetricTests(unittest.TestCase):
    def test_coverage_fairness_compares_least_and_most_served(self) -> None:
        self.assertEqual(coverage_fairness([4, 2]), 0.5)
        self.assertEqual(coverage_fairness([3, 3]), 1.0)
        self.assertEqual(coverage_fairness([0, 0]), 0.0)

    def test_coverage_fairness_rejects_invalid_input(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "at least one traveler count",
        ):
            coverage_fairness([])
        with self.assertRaisesRegex(ValueError, "cannot be negative"):
            coverage_fairness([2, -1])


class ItineraryBenchmarkTests(unittest.TestCase):
    def test_baseline_cases_are_valid_and_reproducible(self) -> None:
        activities = load_activities()
        cases = load_itinerary_cases()

        first_report = evaluate_itineraries(activities, cases)
        second_report = evaluate_itineraries(activities, cases)

        self.assertEqual(first_report, second_report)
        self.assertEqual(first_report.case_count, 6)
        self.assertEqual(first_report.constraint_pass_rate, 1.0)
        self.assertEqual(first_report.exclusion_compliance_rate, 1.0)
        self.assertEqual(first_report.grounding_rate, 1.0)
        self.assertEqual(first_report.must_do_coverage, 1.0)
        self.assertEqual(first_report.shortlist_coverage, 1.0)
        self.assertEqual(first_report.mean_traveler_coverage, 1.0)
        self.assertAlmostEqual(
            first_report.mean_fairness_score,
            0.6662698412698412,
        )
        self.assertEqual(first_report.deterministic_stability_rate, 1.0)

    def test_rejected_must_do_is_excluded_from_coverage_denominator(
        self,
    ) -> None:
        report = evaluate_itineraries(
            load_activities(),
            [
                case
                for case in load_itinerary_cases()
                if case.case_id == "rejected_must_do_override"
            ],
        )
        case_result = report.cases[0]

        self.assertNotIn(
            "rome_colosseum",
            case_result.scheduled_activity_ids,
        )
        self.assertEqual(
            case_result.active_must_do_ids,
            ("rome_borghese_gallery",),
        )
        self.assertEqual(case_result.must_do_coverage, 1.0)

    def test_unknown_case_activity_id_is_rejected(self) -> None:
        activities = load_activities()
        case = load_itinerary_cases()[0].model_copy(
            update={"selected_activity_ids": ["rome_not_in_catalog"]}
        )

        with self.assertRaisesRegex(
            ValueError,
            "references unknown activity IDs",
        ):
            evaluate_itineraries(activities, [case])


if __name__ == "__main__":
    unittest.main()
