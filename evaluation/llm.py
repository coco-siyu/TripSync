"""Evaluate TripSync's structured LLM stories and adjustment proposals."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field

from evaluation.itinerary import ItineraryEvaluationCase, load_itinerary_cases
from evaluation.retrieval import load_activities
from src.llm import (
    NarrationGenerationError,
    generate_itinerary_change_proposals,
    generate_itinerary_narrative,
)
from src.models import Activity, ItineraryPlan, TripSyncModel
from src.must_dos import resolve_must_dos
from src.narration import ItineraryNarrative, NarratedActivity, NarratedDay
from src.planner import apply_itinerary_change_proposal, build_itinerary
from src.proposals import ItineraryChangeProposal, ItineraryChangeProposals
from src.scoring import GroupFitResult, rank_activities
from src.search import retrieve_activities


DEFAULT_CASES_PATH = Path(__file__).with_name("llm_cases.json")
DEFAULT_SUITE_NAME = "contract"


class LlmEvaluationCase(TripSyncModel):
    """One fixed LLM contract scenario with a curated expected behavior."""

    case_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    kind: Literal["narration", "proposal"]
    itinerary_case_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    description: str = Field(min_length=1, max_length=300)
    request: str | None = Field(default=None, max_length=500)


@dataclass(frozen=True)
class LlmCaseResult:
    case_id: str
    kind: str
    schema_passed: bool
    grounding_passed: bool
    complete: bool
    applicable: bool | None
    error: str | None


@dataclass(frozen=True)
class LlmEvaluationReport:
    mode: str
    case_count: int
    schema_pass_rate: float
    grounding_pass_rate: float
    completeness_rate: float
    proposal_applicability_rate: float
    cases: tuple[LlmCaseResult, ...]


def load_llm_cases(path: Path = DEFAULT_CASES_PATH) -> list[LlmEvaluationCase]:
    """Load the fixed LLM benchmark scenarios."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    return [LlmEvaluationCase.model_validate(item) for item in payload]


def _build_plan(
    activities: Sequence[Activity],
    case: ItineraryEvaluationCase,
) -> tuple[ItineraryPlan, list[Activity], list[GroupFitResult], dict[str, tuple[str, ...]]]:
    retrieval = retrieve_activities(activities, case.trip)
    candidates = [
        activity
        for activity in activities
        if activity.id in {result.activity_id for result in retrieval.results}
    ]
    ranked = rank_activities(candidates, case.trip)
    must_dos = resolve_must_dos(activities, case.trip.travelers)
    plan = build_itinerary(
        case.trip,
        candidates,
        ranked,
        case.selected_activity_ids,
        must_do_owners_by_activity_id=must_dos.owners_by_activity_id,
        excluded_activity_ids=case.excluded_activity_ids,
        auto_fill=case.auto_fill,
    )
    owners = {
        activity_id: tuple(names)
        for activity_id, names in must_dos.owners_by_activity_id.items()
    }
    return plan, candidates, ranked, owners


def _fixture_narrative(plan: ItineraryPlan) -> ItineraryNarrative:
    return ItineraryNarrative(
        trip_summary="A grounded itinerary shaped by the group's preferences.",
        days=[
            NarratedDay(
                day_number=day.day_number,
                summary=f"Day {day.day_number} follows the validated schedule.",
                activities=[
                    NarratedActivity(
                        activity_id=activity.activity_id,
                        why_it_fits=activity.reason,
                    )
                    for activity in day.activities
                ],
            )
            for day in plan.days
        ],
    )


def _fixture_proposals(
    plan: ItineraryPlan,
    candidates: Sequence[Activity],
) -> ItineraryChangeProposals:
    scheduled_ids = {
        activity.activity_id for day in plan.days for activity in day.activities
    }
    target_day = next(day for day in plan.days if day.activities)
    removable = target_day.activities[0]
    proposal = ItineraryChangeProposal(
        title="Leave more room to explore",
        operation="remove",
        day_number=target_day.day_number,
        remove_activity_id=removable.activity_id,
        rationale="This creates a deliberate open slot without changing another day.",
        tradeoffs=["One planned activity is removed."],
    )
    for candidate in candidates:
        if candidate.id in scheduled_ids:
            continue
        replacement = proposal.model_copy(
            update={
                "title": f"Swap in {candidate.name}",
                "operation": "replace",
                "add_activity_id": candidate.id,
                "rationale": "A grounded same-day alternative from the catalog.",
            }
        )
        try:
            apply_itinerary_change_proposal(
                plan,
                replacement,
                candidates,
                (),
            )
        except ValueError:
            continue
        proposal = replacement
        break
    return ItineraryChangeProposals(
        acknowledgement="Here is a grounded, reviewable option.",
        proposals=[proposal],
    )


def evaluate_llm_cases(
    activities: Sequence[Activity],
    cases: Sequence[LlmEvaluationCase],
    *,
    live: bool = False,
    suite_name: str = DEFAULT_SUITE_NAME,
) -> LlmEvaluationReport:
    """Run free fixtures or paid live model outputs against one named suite."""

    if not cases:
        raise ValueError("at least one LLM evaluation case is required")
    itinerary_cases = {
        case.case_id: case for case in load_itinerary_cases()
    }
    results: list[LlmCaseResult] = []
    for case in cases:
        source_case = itinerary_cases.get(case.itinerary_case_id)
        if source_case is None:
            raise ValueError(
                f"{case.case_id} references unknown itinerary case "
                f"{case.itinerary_case_id}"
            )
        plan, candidates, ranked, owners = _build_plan(activities, source_case)
        try:
            if case.kind == "narration":
                output = (
                    generate_itinerary_narrative(source_case.trip, candidates, plan)
                    if live
                    else _fixture_narrative(plan)
                )
                expected_ids = [
                    activity.activity_id
                    for day in plan.days
                    for activity in day.activities
                ]
                actual_ids = [
                    activity.activity_id
                    for day in output.days
                    for activity in day.activities
                ]
                results.append(
                    LlmCaseResult(
                        case_id=case.case_id,
                        kind=case.kind,
                        schema_passed=True,
                        grounding_passed=actual_ids == expected_ids,
                        complete=len(output.days) == len(plan.days),
                        applicable=None,
                        error=None,
                    )
                )
            else:
                output = (
                    generate_itinerary_change_proposals(
                        source_case.trip,
                        candidates,
                        plan,
                        case.request or "Suggest a grounded adjustment.",
                    )
                    if live
                    else _fixture_proposals(plan, candidates)
                )
                applicable = False
                for proposal in output.proposals:
                    try:
                        apply_itinerary_change_proposal(
                            plan,
                            proposal,
                            candidates,
                            ranked,
                            must_do_owners_by_activity_id=owners,
                        )
                        applicable = True
                        break
                    except ValueError:
                        continue
                results.append(
                    LlmCaseResult(
                        case_id=case.case_id,
                        kind=case.kind,
                        schema_passed=True,
                        grounding_passed=True,
                        complete=bool(output.proposals),
                        applicable=applicable,
                        error=None,
                    )
                )
        except NarrationGenerationError as error:
            results.append(
                LlmCaseResult(
                    case_id=case.case_id,
                    kind=case.kind,
                    schema_passed=False,
                    grounding_passed=False,
                    complete=False,
                    applicable=False if case.kind == "proposal" else None,
                    error=str(error),
                )
            )

    proposal_results = [result for result in results if result.kind == "proposal"]
    count = len(results)
    return LlmEvaluationReport(
        mode=f"{suite_name} ({'live' if live else 'fixtures'})",
        case_count=count,
        schema_pass_rate=sum(result.schema_passed for result in results) / count,
        grounding_pass_rate=sum(result.grounding_passed for result in results) / count,
        completeness_rate=sum(result.complete for result in results) / count,
        proposal_applicability_rate=(
            sum(bool(result.applicable) for result in proposal_results)
            / len(proposal_results)
            if proposal_results
            else 1.0
        ),
        cases=tuple(results),
    )


def report_as_dict(report: LlmEvaluationReport) -> dict[str, object]:
    return asdict(report)


def format_report(report: LlmEvaluationReport) -> str:
    lines = [
        f"TripSync LLM evaluation ({report.mode})",
        f"Cases: {report.case_count}",
        f"Schema pass rate: {report.schema_pass_rate:.3f}",
        f"Grounding pass rate: {report.grounding_pass_rate:.3f}",
        f"Completeness rate: {report.completeness_rate:.3f}",
        f"Proposal applicability rate: {report.proposal_applicability_rate:.3f}",
    ]
    needs_review = [
        result
        for result in report.cases
        if (
            not result.schema_passed
            or not result.grounding_passed
            or not result.complete
            or result.applicable is False
        )
    ]
    if needs_review:
        lines.extend(["", "Cases needing review:"])
        for result in needs_review:
            checks = [
                f"schema={'pass' if result.schema_passed else 'fail'}",
                f"grounding={'pass' if result.grounding_passed else 'fail'}",
                f"complete={'pass' if result.complete else 'fail'}",
            ]
            if result.applicable is not None:
                checks.append(
                    "applicable="
                    + ("pass" if result.applicable else "fail")
                )
            detail = f"; error={result.error}" if result.error else ""
            lines.append(
                f"- {result.case_id} ({result.kind}): "
                + ", ".join(checks)
                + detail
            )
    else:
        lines.extend(["", "All cases passed the measured contract checks."])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate TripSync LLM narration and itinerary adjustments."
    )
    parser.add_argument("--live", action="store_true", help="Call OpenAI for all cases.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES_PATH,
        help=(
            "Path to an LLM case-suite JSON file "
            f"(default: {DEFAULT_CASES_PATH.name})."
        ),
    )
    args = parser.parse_args()
    suite_name = "held-out robustness" if args.cases.name == "llm_holdout_cases.json" else "contract"
    report = evaluate_llm_cases(
        load_activities(),
        load_llm_cases(args.cases),
        live=args.live,
        suite_name=suite_name,
    )
    if args.json:
        print(json.dumps(report_as_dict(report), indent=2))
    else:
        print(format_report(report))


if __name__ == "__main__":
    main()
