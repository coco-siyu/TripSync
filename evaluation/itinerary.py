"""Evaluate deterministic TripSync itinerary quality and guardrails."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pydantic import Field

from evaluation.retrieval import load_activities
from src.models import Activity, ItineraryPlan, TripRequest, TripSyncModel
from src.must_dos import resolve_must_dos
from src.planner import PACE_RULES, build_itinerary
from src.scoring import rank_activities
from src.search import retrieve_activities


DEFAULT_CASES_PATH = Path(__file__).with_name("itinerary_cases.json")


class ItineraryEvaluationCase(TripSyncModel):
    """One planning scenario used to assess itinerary quality."""

    case_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    description: str = Field(min_length=1, max_length=300)
    trip: TripRequest
    selected_activity_ids: list[str] = Field(default_factory=list)
    excluded_activity_ids: list[str] = Field(default_factory=list)
    auto_fill: bool = True


@dataclass(frozen=True)
class ItineraryCaseResult:
    """Auditable metrics for one generated itinerary."""

    case_id: str
    description: str
    scheduled_activity_ids: tuple[str, ...]
    unscheduled_activity_ids: tuple[str, ...]
    active_must_do_ids: tuple[str, ...]
    constraint_passed: bool
    constraint_violations: tuple[str, ...]
    exclusion_compliant: bool
    grounding_ratio: float
    must_do_coverage: float
    shortlist_coverage: float
    traveler_coverage: float
    traveler_activity_counts: dict[str, int]
    fairness_score: float
    capacity_utilization: float
    deterministic: bool


@dataclass(frozen=True)
class ItineraryEvaluationReport:
    """Aggregate itinerary-quality baseline."""

    case_count: int
    constraint_pass_rate: float
    exclusion_compliance_rate: float
    grounding_rate: float
    must_do_coverage: float
    shortlist_coverage: float
    mean_traveler_coverage: float
    mean_fairness_score: float
    mean_capacity_utilization: float
    deterministic_stability_rate: float
    cases: tuple[ItineraryCaseResult, ...]


def load_itinerary_cases(
    path: Path = DEFAULT_CASES_PATH,
) -> list[ItineraryEvaluationCase]:
    """Load and validate the itinerary benchmark scenarios."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    return [ItineraryEvaluationCase.model_validate(item) for item in payload]


def coverage_fairness(activity_counts: Sequence[int]) -> float:
    """Compare the least-served traveler with the most-served traveler."""

    if not activity_counts:
        raise ValueError("at least one traveler count is required")
    if any(count < 0 for count in activity_counts):
        raise ValueError("traveler activity counts cannot be negative")
    maximum = max(activity_counts)
    if maximum == 0:
        return 0.0
    return min(activity_counts) / maximum


def _scheduled_ids(plan: ItineraryPlan) -> list[str]:
    return [
        scheduled.activity_id
        for day in plan.days
        for scheduled in day.activities
    ]


def _constraint_violations(
    plan: ItineraryPlan,
    case: ItineraryEvaluationCase,
    candidate_ids: set[str],
) -> list[str]:
    """Return hard-constraint failures with human-readable evidence."""

    rule = PACE_RULES[case.trip.pace]
    violations: list[str] = []
    if len(plan.days) != case.trip.days:
        violations.append("trip day count changed")
    if [day.day_number for day in plan.days] != list(
        range(1, case.trip.days + 1)
    ):
        violations.append("day numbers are not sequential")
    if plan.destination.casefold() != case.trip.destination.casefold():
        violations.append("plan destination changed")
    if plan.country.casefold() != case.trip.country.casefold():
        violations.append("plan country changed")

    scheduled_ids = _scheduled_ids(plan)
    if len(scheduled_ids) != len(set(scheduled_ids)):
        violations.append("an activity was scheduled more than once")
    if not set(scheduled_ids).issubset(candidate_ids):
        violations.append(
            "an activity was not grounded in the retrieved candidates"
        )
    if set(scheduled_ids) & set(case.excluded_activity_ids):
        violations.append("an excluded activity was scheduled")
    if plan.auto_fill != case.auto_fill:
        violations.append("the plan changed the automatic-fill setting")
    if not case.auto_fill:
        active_shortlist_ids = (
            set(case.selected_activity_ids)
            - set(case.excluded_activity_ids)
        )
        if not set(scheduled_ids).issubset(active_shortlist_ids):
            violations.append(
                "automatic filling was disabled but a recommendation was added"
            )

    for day in plan.days:
        if day.planned_hours > rule.capacity_hours + 0.01:
            violations.append(
                f"Day {day.day_number} exceeds its time capacity"
            )
        if len(day.activities) > rule.max_activities:
            violations.append(
                f"Day {day.day_number} exceeds its activity limit"
            )
    return violations


def _ratio(found_ids: set[str], expected_ids: set[str]) -> float:
    if not expected_ids:
        return 1.0
    return len(found_ids & expected_ids) / len(expected_ids)


def _traveler_activity_counts(
    plan: ItineraryPlan,
    trip: TripRequest,
) -> dict[str, int]:
    counts = {traveler.name: 0 for traveler in trip.travelers}
    for day in plan.days:
        for scheduled in day.activities:
            for traveler_name in set(scheduled.traveler_names):
                if traveler_name in counts:
                    counts[traveler_name] += 1
    return counts


def _capacity_utilization(plan: ItineraryPlan) -> float:
    total_capacity = sum(day.capacity_hours for day in plan.days)
    if total_capacity == 0:
        return 0.0
    return sum(day.planned_hours for day in plan.days) / total_capacity


def _build_case_plan(
    activities: Sequence[Activity],
    case: ItineraryEvaluationCase,
) -> tuple[
    ItineraryPlan,
    Mapping[str, Sequence[str]],
    set[str],
]:
    retrieval = retrieve_activities(activities, case.trip)
    retrieved_ids = {result.activity_id for result in retrieval.results}
    candidates = [
        activity for activity in activities if activity.id in retrieved_ids
    ]
    ranked_results = rank_activities(candidates, case.trip)
    resolution = resolve_must_dos(activities, case.trip.travelers)
    plan = build_itinerary(
        case.trip,
        candidates,
        ranked_results,
        case.selected_activity_ids,
        must_do_owners_by_activity_id=resolution.owners_by_activity_id,
        excluded_activity_ids=case.excluded_activity_ids,
        auto_fill=case.auto_fill,
    )
    return plan, resolution.owners_by_activity_id, retrieved_ids


def evaluate_itineraries(
    activities: Sequence[Activity],
    cases: Sequence[ItineraryEvaluationCase],
) -> ItineraryEvaluationReport:
    """Generate and evaluate every itinerary scenario."""

    if not cases:
        raise ValueError("at least one evaluation case is required")

    catalog_ids = {activity.id for activity in activities}
    results: list[ItineraryCaseResult] = []
    total_scheduled = 0
    total_grounded = 0
    total_active_must_dos = 0
    total_scheduled_must_dos = 0
    total_shortlisted = 0
    total_scheduled_shortlisted = 0

    for case in cases:
        referenced_ids = {
            *case.selected_activity_ids,
            *case.excluded_activity_ids,
        }
        unknown_ids = referenced_ids - catalog_ids
        if unknown_ids:
            unknown_list = ", ".join(sorted(unknown_ids))
            raise ValueError(
                f"{case.case_id} references unknown activity IDs: "
                f"{unknown_list}"
            )

        plan, owners_by_id, candidate_ids = _build_case_plan(
            activities,
            case,
        )
        repeated_plan, _, repeated_candidate_ids = _build_case_plan(
            activities,
            case,
        )
        deterministic = (
            plan.model_dump(mode="json")
            == repeated_plan.model_dump(mode="json")
            and candidate_ids == repeated_candidate_ids
        )
        scheduled_ids = _scheduled_ids(plan)
        scheduled_id_set = set(scheduled_ids)
        excluded_ids = set(case.excluded_activity_ids)
        active_must_do_ids = set(owners_by_id) - excluded_ids
        active_shortlist_ids = set(case.selected_activity_ids) - excluded_ids
        violations = _constraint_violations(plan, case, candidate_ids)
        traveler_counts = _traveler_activity_counts(plan, case.trip)

        total_scheduled += len(scheduled_ids)
        total_grounded += len(scheduled_id_set & candidate_ids)
        total_active_must_dos += len(active_must_do_ids)
        total_scheduled_must_dos += len(
            scheduled_id_set & active_must_do_ids
        )
        total_shortlisted += len(active_shortlist_ids)
        total_scheduled_shortlisted += len(
            scheduled_id_set & active_shortlist_ids
        )

        results.append(
            ItineraryCaseResult(
                case_id=case.case_id,
                description=case.description,
                scheduled_activity_ids=tuple(scheduled_ids),
                unscheduled_activity_ids=tuple(
                    item.activity_id for item in plan.unscheduled
                ),
                active_must_do_ids=tuple(sorted(active_must_do_ids)),
                constraint_passed=not violations,
                constraint_violations=tuple(violations),
                exclusion_compliant=not (
                    scheduled_id_set & excluded_ids
                ),
                grounding_ratio=(
                    len(scheduled_id_set & candidate_ids)
                    / len(scheduled_ids)
                    if scheduled_ids
                    else 1.0
                ),
                must_do_coverage=_ratio(
                    scheduled_id_set,
                    active_must_do_ids,
                ),
                shortlist_coverage=_ratio(
                    scheduled_id_set,
                    active_shortlist_ids,
                ),
                traveler_coverage=(
                    sum(count > 0 for count in traveler_counts.values())
                    / len(traveler_counts)
                ),
                traveler_activity_counts=traveler_counts,
                fairness_score=coverage_fairness(
                    list(traveler_counts.values())
                ),
                capacity_utilization=_capacity_utilization(plan),
                deterministic=deterministic,
            )
        )

    case_count = len(results)
    return ItineraryEvaluationReport(
        case_count=case_count,
        constraint_pass_rate=(
            sum(result.constraint_passed for result in results) / case_count
        ),
        exclusion_compliance_rate=(
            sum(result.exclusion_compliant for result in results)
            / case_count
        ),
        grounding_rate=(
            total_grounded / total_scheduled if total_scheduled else 1.0
        ),
        must_do_coverage=(
            total_scheduled_must_dos / total_active_must_dos
            if total_active_must_dos
            else 1.0
        ),
        shortlist_coverage=(
            total_scheduled_shortlisted / total_shortlisted
            if total_shortlisted
            else 1.0
        ),
        mean_traveler_coverage=(
            sum(result.traveler_coverage for result in results) / case_count
        ),
        mean_fairness_score=(
            sum(result.fairness_score for result in results) / case_count
        ),
        mean_capacity_utilization=(
            sum(result.capacity_utilization for result in results)
            / case_count
        ),
        deterministic_stability_rate=(
            sum(result.deterministic for result in results) / case_count
        ),
        cases=tuple(results),
    )


def report_as_dict(report: ItineraryEvaluationReport) -> dict[str, Any]:
    """Convert a report into a JSON-serializable dictionary."""

    return asdict(report)


def format_report(report: ItineraryEvaluationReport) -> str:
    """Render a compact human-readable itinerary benchmark."""

    metrics = [
        ("Constraint pass rate", report.constraint_pass_rate),
        ("Exclusion compliance", report.exclusion_compliance_rate),
        ("Retrieved-candidate grounding", report.grounding_rate),
        ("Must-do coverage", report.must_do_coverage),
        ("Shortlist coverage", report.shortlist_coverage),
        ("Mean traveler coverage", report.mean_traveler_coverage),
        ("Mean fairness", report.mean_fairness_score),
        ("Mean capacity utilization", report.mean_capacity_utilization),
        ("Deterministic stability", report.deterministic_stability_rate),
    ]
    lines = [
        "TripSync deterministic itinerary baseline",
        f"Cases: {report.case_count}",
        *[f"{label}: {value:.3f}" for label, value in metrics],
        "",
        "Per-case fairness and utilization:",
    ]
    for result in report.cases:
        status = "pass" if result.constraint_passed else "fail"
        lines.append(
            f"- {result.case_id}: constraints={status}, "
            f"fairness={result.fairness_score:.3f}, "
            f"utilization={result.capacity_utilization:.3f}"
        )
    return "\n".join(lines)


def main() -> None:
    """Run the itinerary benchmark from the command line."""

    parser = argparse.ArgumentParser(
        description="Evaluate TripSync deterministic itinerary planning."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of the text summary.",
    )
    args = parser.parse_args()

    report = evaluate_itineraries(
        load_activities(),
        load_itinerary_cases(),
    )
    if args.json:
        print(json.dumps(report_as_dict(report), indent=2))
    else:
        print(format_report(report))


if __name__ == "__main__":
    main()
