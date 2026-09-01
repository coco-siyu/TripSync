"""Evaluate TripSync retrieval against a small labeled query set."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pydantic import Field

from src.models import Activity, TripRequest, TripSyncModel
from src.search import EmbeddingModel, RetrievalMode, retrieve_activities


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ACTIVITIES_PATH = REPOSITORY_ROOT / "data" / "activities.json"
DEFAULT_CASES_PATH = Path(__file__).with_name("retrieval_cases.json")


class RetrievalEvaluationCase(TripSyncModel):
    """One labeled trip request and its acceptable retrieval results."""

    case_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    description: str = Field(min_length=1, max_length=300)
    trip: TripRequest
    relevant_activity_ids: list[str] = Field(min_length=1)


@dataclass(frozen=True)
class RankedRetrievalMetrics:
    """Metrics for one ranked result list."""

    retrieved_ids: tuple[str, ...]
    relevant_ranks: tuple[int, ...]
    first_relevant_rank: int | None
    hit: bool
    reciprocal_rank: float
    recall: float


@dataclass(frozen=True)
class RetrievalCaseResult:
    """Evaluation output for one labeled case."""

    case_id: str
    description: str
    relevant_activity_ids: tuple[str, ...]
    metrics: RankedRetrievalMetrics
    semantic_fallback: bool
    semantic_unavailable_reason: str | None


@dataclass(frozen=True)
class RetrievalEvaluationReport:
    """Aggregate retrieval metrics plus auditable per-case results."""

    mode: RetrievalMode
    k: int
    case_count: int
    hit_rate: float
    mean_reciprocal_rank: float
    mean_recall: float
    semantic_fallback_count: int
    cases: tuple[RetrievalCaseResult, ...]


@dataclass(frozen=True)
class RetrievalComparisonResult:
    """One retrieval mode's report within a like-for-like comparison."""

    mode: RetrievalMode
    report: RetrievalEvaluationReport


@dataclass(frozen=True)
class RetrievalComparisonReport:
    """Side-by-side reports for the same catalog, cases, and cutoff."""

    k: int
    case_count: int
    results: tuple[RetrievalComparisonResult, ...]


def load_activities(path: Path = DEFAULT_ACTIVITIES_PATH) -> list[Activity]:
    """Load and validate the grounded activity catalog."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    return [Activity.model_validate(item) for item in payload]


def load_retrieval_cases(
    path: Path = DEFAULT_CASES_PATH,
) -> list[RetrievalEvaluationCase]:
    """Load and validate the labeled retrieval benchmark."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    return [RetrievalEvaluationCase.model_validate(item) for item in payload]


def score_ranked_ids(
    retrieved_ids: Sequence[str],
    relevant_activity_ids: Sequence[str],
    *,
    k: int,
) -> RankedRetrievalMetrics:
    """Calculate Hit Rate, reciprocal rank, and recall for one query."""

    if k < 1:
        raise ValueError("k must be at least 1")
    relevant_ids = set(relevant_activity_ids)
    if not relevant_ids:
        raise ValueError("at least one relevant activity ID is required")

    top_ids = tuple(retrieved_ids[:k])
    relevant_ranks = tuple(
        rank
        for rank, activity_id in enumerate(top_ids, start=1)
        if activity_id in relevant_ids
    )
    first_relevant_rank = relevant_ranks[0] if relevant_ranks else None

    return RankedRetrievalMetrics(
        retrieved_ids=top_ids,
        relevant_ranks=relevant_ranks,
        first_relevant_rank=first_relevant_rank,
        hit=first_relevant_rank is not None,
        reciprocal_rank=(
            1 / first_relevant_rank if first_relevant_rank is not None else 0
        ),
        recall=len(set(top_ids) & relevant_ids) / len(relevant_ids),
    )


def evaluate_retrieval(
    activities: Sequence[Activity],
    cases: Sequence[RetrievalEvaluationCase],
    *,
    k: int = 5,
    mode: RetrievalMode = "text",
    embedding_model: EmbeddingModel | None = None,
) -> RetrievalEvaluationReport:
    """Run one retrieval mode over every labeled case."""

    if k < 1:
        raise ValueError("k must be at least 1")
    if not cases:
        raise ValueError("at least one evaluation case is required")

    catalog_ids = {activity.id for activity in activities}
    results: list[RetrievalCaseResult] = []
    for case in cases:
        unknown_ids = set(case.relevant_activity_ids) - catalog_ids
        if unknown_ids:
            unknown_list = ", ".join(sorted(unknown_ids))
            raise ValueError(
                f"{case.case_id} references unknown activity IDs: "
                f"{unknown_list}"
            )

        response = retrieve_activities(
            activities,
            case.trip,
            limit=k,
            mode=mode,
            embedding_model=embedding_model,
        )
        ranked_ids = [result.activity_id for result in response.results]
        metrics = score_ranked_ids(
            ranked_ids,
            case.relevant_activity_ids,
            k=k,
        )
        results.append(
            RetrievalCaseResult(
                case_id=case.case_id,
                description=case.description,
                relevant_activity_ids=tuple(case.relevant_activity_ids),
                metrics=metrics,
                semantic_fallback=response.semantic_fallback,
                semantic_unavailable_reason=response.semantic_unavailable_reason,
            )
        )

    case_count = len(results)
    return RetrievalEvaluationReport(
        mode=mode,
        k=k,
        case_count=case_count,
        hit_rate=sum(result.metrics.hit for result in results) / case_count,
        mean_reciprocal_rank=(
            sum(result.metrics.reciprocal_rank for result in results)
            / case_count
        ),
        mean_recall=(
            sum(result.metrics.recall for result in results) / case_count
        ),
        semantic_fallback_count=sum(
            result.semantic_fallback for result in results
        ),
        cases=tuple(results),
    )


def compare_retrieval_modes(
    activities: Sequence[Activity],
    cases: Sequence[RetrievalEvaluationCase],
    *,
    k: int = 5,
    embedding_model: EmbeddingModel | None = None,
) -> RetrievalComparisonReport:
    """Evaluate text, vector, and hybrid modes against identical labels."""

    reports = tuple(
        RetrievalComparisonResult(
            mode=mode,
            report=evaluate_retrieval(
                activities,
                cases,
                k=k,
                mode=mode,
                embedding_model=embedding_model,
            ),
        )
        for mode in ("text", "vector", "hybrid")
    )
    return RetrievalComparisonReport(
        k=k,
        case_count=len(cases),
        results=reports,
    )


def report_as_dict(report: RetrievalEvaluationReport) -> dict[str, Any]:
    """Convert a report into a JSON-serializable dictionary."""

    return asdict(report)


def format_report(report: RetrievalEvaluationReport) -> str:
    """Render a compact, human-readable benchmark report."""

    lines = [
        f"TripSync retrieval evaluation (k={report.k})",
        f"Cases: {report.case_count}",
        f"Hit Rate@{report.k}: {report.hit_rate:.3f}",
        f"MRR@{report.k}: {report.mean_reciprocal_rank:.3f}",
        f"Mean Recall@{report.k}: {report.mean_recall:.3f}",
        (
            "Mode: text (semantic retrieval not requested)"
            if report.mode == "text"
            else (
                "Semantic retrieval: active"
                if report.semantic_fallback_count == 0
                else (
                    "Semantic retrieval: unavailable for "
                    f"{report.semantic_fallback_count}/{report.case_count} case(s); "
                    "those results used text fallback."
                )
            )
        ),
        "",
        "Per-case first relevant rank:",
    ]
    for result in report.cases:
        rank = result.metrics.first_relevant_rank
        rank_label = str(rank) if rank is not None else "miss"
        fallback_suffix = (
            f" (text fallback: {result.semantic_unavailable_reason})"
            if result.semantic_fallback
            else ""
        )
        lines.append(f"- {result.case_id}: {rank_label}{fallback_suffix}")
    return "\n".join(lines)


def format_comparison_report(report: RetrievalComparisonReport) -> str:
    """Render compact, side-by-side retrieval metrics for decisions."""

    lines = [
        f"TripSync retrieval comparison (k={report.k})",
        f"Cases: {report.case_count}",
        "",
        "Mode     Hit Rate@K   MRR@K   Mean Recall@K",
    ]
    for result in report.results:
        metrics = result.report
        lines.append(
            f"{result.mode:<8} {metrics.hit_rate:>10.3f} "
            f"{metrics.mean_reciprocal_rank:>7.3f} "
            f"{metrics.mean_recall:>15.3f}"
        )

    best_mrr = max(
        result.report.mean_reciprocal_rank for result in report.results
    )
    best_recall = max(result.report.mean_recall for result in report.results)
    best_mrr_modes = ", ".join(
        result.mode
        for result in report.results
        if result.report.mean_reciprocal_rank == best_mrr
    )
    best_recall_modes = ", ".join(
        result.mode
        for result in report.results
        if result.report.mean_recall == best_recall
    )
    lines.extend(
        [
            "",
            f"Best MRR@{report.k}: {best_mrr_modes} ({best_mrr:.3f})",
            f"Best mean recall@{report.k}: {best_recall_modes} ({best_recall:.3f})",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    """Run the retrieval benchmark from the command line."""

    parser = argparse.ArgumentParser(
        description="Evaluate TripSync deterministic text retrieval."
    )
    parser.add_argument(
        "--k",
        type=int,
        default=5,
        help="Evaluate the first K retrieved activities (default: 5).",
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES_PATH,
        help="Path to a labeled retrieval-case JSON file.",
    )
    parser.add_argument("--mode", choices=["text", "vector", "hybrid"], default="text")
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Compare text, vector, and hybrid modes on the same cases.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of the text summary.",
    )
    args = parser.parse_args()

    activities = load_activities()
    cases = load_retrieval_cases(args.cases)
    if args.compare:
        report = compare_retrieval_modes(activities, cases, k=args.k)
        if args.json:
            print(json.dumps(asdict(report), indent=2))
        else:
            print(format_comparison_report(report))
        return

    report = evaluate_retrieval(activities, cases, k=args.k, mode=args.mode)
    if args.json:
        print(json.dumps(report_as_dict(report), indent=2))
    else:
        print(format_report(report))


if __name__ == "__main__":
    main()
