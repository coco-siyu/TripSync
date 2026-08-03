"""Tests for the fixed LLM contract-evaluation baseline."""

from __future__ import annotations

import unittest
from pathlib import Path

from evaluation.llm import (
    LlmCaseResult,
    LlmEvaluationReport,
    compare_models,
    evaluate_llm_cases,
    format_model_comparison,
    format_report,
    load_llm_cases,
)
from evaluation.retrieval import load_activities


class LlmEvaluationTests(unittest.TestCase):
    def test_fixture_baseline_has_twenty_reproducible_cases(self) -> None:
        activities = load_activities()
        cases = load_llm_cases()

        first_report = evaluate_llm_cases(activities, cases)
        second_report = evaluate_llm_cases(activities, cases)

        self.assertEqual(first_report, second_report)
        self.assertEqual(first_report.case_count, 20)
        self.assertEqual(
            sum(case.kind == "narration" for case in cases),
            10,
        )
        self.assertEqual(
            sum(case.kind == "proposal" for case in cases),
            10,
        )
        self.assertEqual(first_report.schema_pass_rate, 1.0)
        self.assertEqual(first_report.grounding_pass_rate, 1.0)
        self.assertEqual(first_report.completeness_rate, 1.0)
        self.assertEqual(first_report.proposal_applicability_rate, 1.0)

    def test_report_names_cases_that_need_review(self) -> None:
        report = LlmEvaluationReport(
            mode="live",
            case_count=1,
            schema_pass_rate=0.0,
            grounding_pass_rate=0.0,
            completeness_rate=0.0,
            proposal_applicability_rate=0.0,
            cases=(
                LlmCaseResult(
                    case_id="adjust_balanced_calmer",
                    kind="proposal",
                    schema_passed=False,
                    grounding_passed=False,
                    complete=False,
                    applicable=False,
                    error="OpenAI returned an unusable response.",
                ),
            ),
        )

        rendered = format_report(report)

        self.assertIn("Cases needing review:", rendered)
        self.assertIn("adjust_balanced_calmer", rendered)
        self.assertIn("error=OpenAI returned an unusable response.", rendered)

    def test_held_out_suite_is_a_separate_reproducible_baseline(self) -> None:
        activities = load_activities()
        held_out_path = (
            Path(__file__).parents[1]
            / "evaluation"
            / "llm_holdout_cases.json"
        )
        cases = load_llm_cases(held_out_path)

        report = evaluate_llm_cases(
            activities,
            cases,
            suite_name="held-out robustness",
        )

        self.assertEqual(report.case_count, 12)
        self.assertEqual(sum(case.kind == "narration" for case in cases), 4)
        self.assertEqual(sum(case.kind == "proposal" for case in cases), 8)
        self.assertEqual(report.schema_pass_rate, 1.0)
        self.assertEqual(report.proposal_applicability_rate, 1.0)

    def test_model_comparison_reports_tie_for_identical_fixture_contracts(self) -> None:
        reports = compare_models(
            load_activities(),
            load_llm_cases()[:2],
            ["model-a", "model-b"],
        )

        rendered = format_model_comparison(reports)

        self.assertEqual([report.model for report in reports], ["model-a", "model-b"])
        self.assertIn("model-a", rendered)
        self.assertIn("Measured contract result: tie", rendered)


if __name__ == "__main__":
    unittest.main()
