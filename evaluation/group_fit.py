"""Evaluate whether TripSync's ranked recommendations serve a whole group."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field

from evaluation.retrieval import (
    EmbeddingModel,
    RetrievalMode,
    RankedRetrievalMetrics,
    load_activities,
    score_ranked_ids,
)
from src.models import Activity, TripRequest, TripSyncModel
from src.scoring import GroupFitResult, rank_activities
from src.search import retrieve_activities


DEFAULT_CASES_PATH = Path(__file__).with_name("group_fit_cases.json")
DEFAULT_HOLDOUT_CASES_PATH = Path(__file__).with_name("group_fit_holdout_cases.json")


class GroupFitEvaluationCase(TripSyncModel):
    """A reviewed group request with acceptable ranked outcomes."""

    case_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    description: str = Field(min_length=1, max_length=300)
    trip: TripRequest
    expected_top_activity_ids: list[str] = Field(min_length=1)
    expected_activity_ids_by_traveler: dict[str, list[str]] = Field(
        default_factory=dict
    )


@dataclass(frozen=True)
class GroupFitCaseResult:
    """Auditable recommendation-quality results for one group."""

    case_id: str
    description: str
    metrics: RankedRetrievalMetrics
    top_activity_ids: tuple[str, ...]
    traveler_target_hits: dict[str, bool]
    semantic_fallback: bool
    semantic_unavailable_reason: str | None


@dataclass(frozen=True)
class GroupFitEvaluationReport:
    """Aggregate quality metrics for group recommendation scenarios."""

    k: int
    mode: RetrievalMode
    case_count: int
    top_five_hit_rate: float
    mean_reciprocal_rank: float
    mean_expected_activity_recall: float
    traveler_target_coverage: float
    semantic_fallback_count: int
    cases: tuple[GroupFitCaseResult, ...]


def load_group_fit_cases(
    path: Path = DEFAULT_CASES_PATH,
) -> list[GroupFitEvaluationCase]:
    """Load and validate the reviewed end-to-end recommendation scenarios."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    return [GroupFitEvaluationCase.model_validate(item) for item in payload]


def _validate_case(
    case: GroupFitEvaluationCase,
    catalog_ids: set[str],
) -> None:
    referenced_ids = set(case.expected_top_activity_ids)
    referenced_ids.update(
        activity_id
        for activity_ids in case.expected_activity_ids_by_traveler.values()
        for activity_id in activity_ids
    )
    unknown_ids = referenced_ids - catalog_ids
    if unknown_ids:
        raise ValueError(
            f"{case.case_id} references unknown activity IDs: "
            f"{', '.join(sorted(unknown_ids))}"
        )

    traveler_names = {traveler.name for traveler in case.trip.travelers}
    unknown_travelers = (
        set(case.expected_activity_ids_by_traveler) - traveler_names
    )
    if unknown_travelers:
        raise ValueError(
            f"{case.case_id} references unknown travelers: "
            f"{', '.join(sorted(unknown_travelers))}"
        )


def _traveler_is_served(
    result: GroupFitResult,
    traveler_name: str,
) -> bool:
    """Return whether a result gives this traveler a preference or must-do benefit."""

    fit = next(
        fit for fit in result.traveler_fits if fit.traveler_name == traveler_name
    )
    return bool(fit.interest_score or fit.must_do_score)


def evaluate_group_fit(
    activities: Sequence[Activity],
    cases: Sequence[GroupFitEvaluationCase],
    *,
    k: int = 5,
    mode: RetrievalMode = "hybrid",
    embedding_model: EmbeddingModel | None = None,
) -> GroupFitEvaluationReport:
    """Measure Top-K relevance and traveler coverage after full ranking."""

    if k < 1:
        raise ValueError("k must be at least 1")
    if not cases:
        raise ValueError("at least one evaluation case is required")

    activity_by_id = {activity.id: activity for activity in activities}
    results: list[GroupFitCaseResult] = []
    target_count = 0
    target_hits = 0

    for case in cases:
        _validate_case(case, set(activity_by_id))
        response = retrieve_activities(
            activities,
            case.trip,
            mode=mode,
            embedding_model=embedding_model,
        )
        ranked = rank_activities(
            [activity_by_id[item.activity_id] for item in response.results],
            case.trip,
            semantic_similarities_by_activity={
                item.activity_id: dict(item.semantic_similarities)
                for item in response.results
            },
        )
        top_results = ranked[:k]
        top_ids = tuple(result.activity_id for result in top_results)
        metrics = score_ranked_ids(
            top_ids,
            case.expected_top_activity_ids,
            k=k,
        )

        traveler_target_hits: dict[str, bool] = {}
        for traveler_name, expected_ids in case.expected_activity_ids_by_traveler.items():
            served = any(
                result.activity_id in expected_ids
                and _traveler_is_served(result, traveler_name)
                for result in top_results
            )
            traveler_target_hits[traveler_name] = served
            target_count += 1
            target_hits += served

        results.append(
            GroupFitCaseResult(
                case_id=case.case_id,
                description=case.description,
                metrics=metrics,
                top_activity_ids=top_ids,
                traveler_target_hits=traveler_target_hits,
                semantic_fallback=response.semantic_fallback,
                semantic_unavailable_reason=response.semantic_unavailable_reason,
            )
        )

    case_count = len(results)
    return GroupFitEvaluationReport(
        k=k,
        mode=mode,
        case_count=case_count,
        top_five_hit_rate=sum(item.metrics.hit for item in results) / case_count,
        mean_reciprocal_rank=(
            sum(item.metrics.reciprocal_rank for item in results) / case_count
        ),
        mean_expected_activity_recall=(
            sum(item.metrics.recall for item in results) / case_count
        ),
        traveler_target_coverage=(target_hits / target_count if target_count else 1.0),
        semantic_fallback_count=sum(item.semantic_fallback for item in results),
        cases=tuple(results),
    )


def format_report(report: GroupFitEvaluationReport) -> str:
    """Render a compact summary suitable for a project readme or terminal."""

    lines = [
        f"TripSync group-fit evaluation (Top {report.k})",
        f"Cases: {report.case_count}",
        f"Top-{report.k} hit rate: {report.top_five_hit_rate:.3f}",
        f"MRR@{report.k}: {report.mean_reciprocal_rank:.3f}",
        f"Expected activity recall@{report.k}: {report.mean_expected_activity_recall:.3f}",
        f"Traveler target coverage: {report.traveler_target_coverage:.3f}",
        (
            "Semantic retrieval: not requested (text mode)"
            if report.mode == "text"
            else (
                "Semantic retrieval: active"
                if not report.semantic_fallback_count
                else (
                    "Semantic retrieval: unavailable for "
                    f"{report.semantic_fallback_count}/{report.case_count} case(s); "
                    "those results used text fallback."
                )
            )
        ),
        "",
        "Per-case Top-K result:",
    ]
    for case in report.cases:
        rank = case.metrics.first_relevant_rank
        rank_label = str(rank) if rank is not None else "miss"
        coverage = ", ".join(
            f"{name}={'yes' if hit else 'no'}"
            for name, hit in case.traveler_target_hits.items()
        ) or "no traveler targets"
        fallback = (
            f" (text fallback: {case.semantic_unavailable_reason})"
            if case.semantic_fallback
            else ""
        )
        lines.append(f"- {case.case_id}: rank {rank_label}; {coverage}{fallback}")
    return "\n".join(lines)


def main() -> None:
    """Run the end-to-end recommendation-quality benchmark."""

    parser = argparse.ArgumentParser(
        description="Evaluate TripSync group-fit recommendations."
    )
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument(
        "--holdout",
        action="store_true",
        help="Use the separately reviewed holdout scenarios.",
    )
    parser.add_argument(
        "--mode", choices=["text", "vector", "hybrid"], default="hybrid"
    )
    args = parser.parse_args()

    cases_path = DEFAULT_HOLDOUT_CASES_PATH if args.holdout else args.cases

    report = evaluate_group_fit(
        load_activities(),
        load_group_fit_cases(cases_path),
        k=args.k,
        mode=args.mode,
    )
    print(format_report(report))


if __name__ == "__main__":
    main()
