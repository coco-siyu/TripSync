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
from src.search import retrieve_activities


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ACTIVITIES_PATH = REPOSITORY_ROOT / "data" / "sample_activities.json"
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


@dataclass(frozen=True)
class RetrievalEvaluationReport:
    """Aggregate retrieval metrics plus auditable per-case results."""

    k: int
    case_count: int
    hit_rate: float
    mean_reciprocal_rank: float
    mean_recall: float
    cases: tuple[RetrievalCaseResult, ...]


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
) -> RetrievalEvaluationReport:
    """Run deterministic text retrieval over every labeled case."""

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

        response = retrieve_activities(activities, case.trip, limit=k)
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
            )
        )

    case_count = len(results)
    return RetrievalEvaluationReport(
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
        cases=tuple(results),
    )


def report_as_dict(report: RetrievalEvaluationReport) -> dict[str, Any]:
    """Convert a report into a JSON-serializable dictionary."""

    return asdict(report)


def format_report(report: RetrievalEvaluationReport) -> str:
    """Render a compact, human-readable benchmark report."""

    lines = [
        f"TripSync text retrieval baseline (k={report.k})",
        f"Cases: {report.case_count}",
        f"Hit Rate@{report.k}: {report.hit_rate:.3f}",
        f"MRR@{report.k}: {report.mean_reciprocal_rank:.3f}",
        f"Mean Recall@{report.k}: {report.mean_recall:.3f}",
        "",
        "Per-case first relevant rank:",
    ]
    for result in report.cases:
        rank = result.metrics.first_relevant_rank
        rank_label = str(rank) if rank is not None else "miss"
        lines.append(f"- {result.case_id}: {rank_label}")
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
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of the text summary.",
    )
    args = parser.parse_args()

    report = evaluate_retrieval(
        load_activities(),
        load_retrieval_cases(),
        k=args.k,
    )
    if args.json:
        print(json.dumps(report_as_dict(report), indent=2))
    else:
        print(format_report(report))


if __name__ == "__main__":
    main()
