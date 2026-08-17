"""Tests for the deterministic retrieval benchmark."""

from __future__ import annotations

import unittest
from pathlib import Path

from evaluation.retrieval import (
    compare_retrieval_modes,
    evaluate_retrieval,
    format_comparison_report,
    load_activities,
    load_retrieval_cases,
    score_ranked_ids,
)
from evaluation.group_fit import evaluate_group_fit, load_group_fit_cases


class FakeEmbeddingModel:
    """Small deterministic embedding stand-in for comparison tests."""

    def encode(self, sentences, *, normalize_embeddings):
        vectors = []
        for sentence in sentences:
            lower = sentence.lower()
            vectors.append([
                float("art" in lower or "sculpture" in lower),
                float("history" in lower or "architecture" in lower),
            ])
        return vectors


class RankedRetrievalMetricTests(unittest.TestCase):
    def test_metrics_use_first_relevant_rank_and_unique_recall(self) -> None:
        metrics = score_ranked_ids(
            ["not_relevant", "relevant_b", "relevant_a", "relevant_a"],
            ["relevant_a", "relevant_b", "relevant_c"],
            k=4,
        )

        self.assertTrue(metrics.hit)
        self.assertEqual(metrics.first_relevant_rank, 2)
        self.assertEqual(metrics.relevant_ranks, (2, 3, 4))
        self.assertEqual(metrics.reciprocal_rank, 0.5)
        self.assertAlmostEqual(metrics.recall, 2 / 3)

    def test_miss_has_zero_reciprocal_rank_and_recall(self) -> None:
        metrics = score_ranked_ids(
            ["first", "second"],
            ["relevant"],
            k=2,
        )

        self.assertFalse(metrics.hit)
        self.assertIsNone(metrics.first_relevant_rank)
        self.assertEqual(metrics.reciprocal_rank, 0)
        self.assertEqual(metrics.recall, 0)

    def test_invalid_k_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "k must be at least 1"):
            score_ranked_ids(["activity"], ["activity"], k=0)


class RetrievalBenchmarkTests(unittest.TestCase):
    def test_baseline_cases_are_valid_and_reproducible(self) -> None:
        activities = load_activities()
        cases = load_retrieval_cases()

        first_report = evaluate_retrieval(activities, cases, k=5)
        second_report = evaluate_retrieval(activities, cases, k=5)

        self.assertEqual(first_report, second_report)
        self.assertEqual(first_report.case_count, 12)
        self.assertGreaterEqual(first_report.hit_rate, 0.9)
        self.assertGreaterEqual(first_report.mean_reciprocal_rank, 0.8)
        self.assertGreaterEqual(first_report.mean_recall, 0.8)
        self.assertEqual(first_report.semantic_fallback_count, 0)

    def test_unknown_ground_truth_id_is_rejected(self) -> None:
        activities = load_activities()
        case = load_retrieval_cases()[0]
        invalid_case = case.model_copy(
            update={"relevant_activity_ids": ["rome_not_in_catalog"]}
        )

        with self.assertRaisesRegex(
            ValueError,
            "references unknown activity IDs",
        ):
            evaluate_retrieval(activities, [invalid_case])

    def test_semantic_holdout_cases_are_valid(self) -> None:
        cases_path = (
            Path(__file__).resolve().parents[1]
            / "evaluation"
            / "retrieval_semantic_cases.json"
        )
        cases = load_retrieval_cases(cases_path)

        self.assertEqual(len(cases), 6)
        self.assertTrue(
            all(
                not set(case.trip.travelers[0].interests)
                & {"art", "history", "food", "nature", "relaxation"}
                for case in cases
            )
        )

    def test_comparison_uses_all_modes_with_identical_cases(self) -> None:
        activities = load_activities()
        cases = load_retrieval_cases()[:2]

        comparison = compare_retrieval_modes(
            activities,
            cases,
            k=5,
            embedding_model=FakeEmbeddingModel(),
        )

        self.assertEqual(comparison.case_count, 2)
        self.assertEqual(
            [result.mode for result in comparison.results],
            ["text", "vector", "hybrid"],
        )
        self.assertIn("Best MRR@5", format_comparison_report(comparison))


class GroupFitBenchmarkTests(unittest.TestCase):
    def test_group_fit_cases_are_valid_and_cover_each_traveler_in_text_baseline(self) -> None:
        report = evaluate_group_fit(
            load_activities(),
            load_group_fit_cases(),
            k=5,
            mode="text",
        )

        self.assertEqual(report.case_count, 10)
        self.assertEqual(report.mode, "text")
        self.assertEqual(report.semantic_fallback_count, 0)
        self.assertGreaterEqual(report.top_five_hit_rate, 0.9)
        self.assertGreaterEqual(report.traveler_target_coverage, 0.8)

    def test_group_fit_holdout_cases_are_valid_and_separate_from_baseline(self) -> None:
        root = Path(__file__).resolve().parents[1]
        baseline_ids = {
            case.case_id for case in load_group_fit_cases(root / "evaluation" / "group_fit_cases.json")
        }
        holdout_cases = load_group_fit_cases(
            root / "evaluation" / "group_fit_holdout_cases.json"
        )

        report = evaluate_group_fit(
            load_activities(),
            holdout_cases,
            k=5,
            mode="text",
        )

        self.assertEqual(report.case_count, 6)
        self.assertTrue(baseline_ids.isdisjoint(case.case_id for case in holdout_cases))
        self.assertEqual(report.semantic_fallback_count, 0)


if __name__ == "__main__":
    unittest.main()
