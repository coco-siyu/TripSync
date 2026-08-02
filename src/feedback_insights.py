"""Privacy-conscious aggregation and export of local TripSync feedback."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from src.feedback import (
    DEFAULT_FEEDBACK_DATABASE_PATH,
    FeedbackRecord,
    OverallExperienceRecord,
    list_feedback,
    list_overall_experience_feedback,
)


@dataclass(frozen=True)
class FeedbackComment:
    """One optional comment without session or itinerary identifiers."""

    source: str
    rating: str
    comment: str
    created_at: str


@dataclass(frozen=True)
class FeedbackInsights:
    """Aggregate feedback suitable for a small human-review dashboard."""

    overall_response_count: int
    story_up: int
    story_down: int
    adjustment_up: int
    adjustment_down: int
    helpfulness_average: float | None
    clarity_average: float | None
    group_fit_average: float | None
    comments: tuple[FeedbackComment, ...]

    @property
    def generated_feedback_count(self) -> int:
        return self.story_up + self.story_down + self.adjustment_up + self.adjustment_down


def _average(values: Iterable[int]) -> float | None:
    values = tuple(values)
    return round(sum(values) / len(values), 2) if values else None


def build_feedback_insights(
    feedback: Iterable[FeedbackRecord],
    overall_experience: Iterable[OverallExperienceRecord],
) -> FeedbackInsights:
    """Build review metrics while deliberately excluding anonymous IDs."""

    generated = tuple(feedback)
    overall = tuple(overall_experience)
    counts = {
        (target_type, rating): sum(
            record.target_type == target_type and record.rating == rating
            for record in generated
        )
        for target_type in ("trip_story", "adjustment_proposal")
        for rating in ("up", "down")
    }
    source_names = {
        "trip_story": "Trip story",
        "adjustment_proposal": "Adjustment suggestion",
    }
    comments = [
        FeedbackComment(
            source=source_names[record.target_type],
            rating="Helpful" if record.rating == "up" else "Not useful",
            comment=record.comment,
            created_at=record.created_at,
        )
        for record in generated
        if record.comment
    ]
    comments.extend(
        FeedbackComment(
            source="Overall plan",
            rating=f"{(record.helpfulness + record.clarity + record.group_fit) / 3:.1f}/5",
            comment=record.comment,
            created_at=record.created_at,
        )
        for record in overall
        if record.comment
    )
    comments.sort(key=lambda comment: comment.created_at, reverse=True)

    return FeedbackInsights(
        overall_response_count=len(overall),
        story_up=counts[("trip_story", "up")],
        story_down=counts[("trip_story", "down")],
        adjustment_up=counts[("adjustment_proposal", "up")],
        adjustment_down=counts[("adjustment_proposal", "down")],
        helpfulness_average=_average(record.helpfulness for record in overall),
        clarity_average=_average(record.clarity for record in overall),
        group_fit_average=_average(record.group_fit for record in overall),
        comments=tuple(comments),
    )


def load_feedback_insights(
    database_path: Path = DEFAULT_FEEDBACK_DATABASE_PATH,
) -> FeedbackInsights:
    """Load feedback from the local SQLite database for human review."""

    return build_feedback_insights(
        list_feedback(database_path),
        list_overall_experience_feedback(database_path),
    )


def feedback_insights_as_dict(insights: FeedbackInsights) -> dict:
    """Return a JSON-safe export that intentionally omits anonymous IDs."""

    return {
        "overall_response_count": insights.overall_response_count,
        "generated_feedback_count": insights.generated_feedback_count,
        "thumb_feedback": {
            "trip_story": {"up": insights.story_up, "down": insights.story_down},
            "adjustment_proposal": {
                "up": insights.adjustment_up,
                "down": insights.adjustment_down,
            },
        },
        "overall_ratings": {
            "helpfulness_average": insights.helpfulness_average,
            "clarity_average": insights.clarity_average,
            "group_fit_average": insights.group_fit_average,
        },
        "comments": [asdict(comment) for comment in insights.comments],
    }


def feedback_dashboard_data(
    feedback: Iterable[FeedbackRecord],
    overall_experience: Iterable[OverallExperienceRecord],
) -> dict[str, list[dict[str, object]]]:
    """Build chart-ready aggregates without retaining anonymous identifiers.

    The UI receives only dates, labels, scores, and counts. Session, itinerary,
    and generated-output identifiers never leave this aggregation layer.
    """

    generated = tuple(feedback)
    overall = tuple(overall_experience)
    source_names = {
        "trip_story": "Trip stories",
        "adjustment_proposal": "Adjustment suggestions",
    }

    daily_counts: dict[tuple[str, str], int] = {}
    for record in generated:
        day = _feedback_day(record.created_at)
        key = (day, source_names[record.target_type])
        daily_counts[key] = daily_counts.get(key, 0) + 1
    for record in overall:
        day = _feedback_day(record.created_at)
        key = (day, "Overall plans")
        daily_counts[key] = daily_counts.get(key, 0) + 1

    source_counts: dict[tuple[str, str], int] = {}
    for record in generated:
        key = (source_names[record.target_type], "Helpful" if record.rating == "up" else "Not useful")
        source_counts[key] = source_counts.get(key, 0) + 1

    helpful_rates = []
    for source in source_names.values():
        helpful = source_counts.get((source, "Helpful"), 0)
        total = helpful + source_counts.get((source, "Not useful"), 0)
        if total:
            helpful_rates.append({"Experience": source, "Helpful rate": round(helpful / total * 100, 1)})

    rating_fields = (
        ("Helpfulness", "helpfulness"),
        ("Clarity", "clarity"),
        ("Group fit", "group_fit"),
    )
    rating_averages = [
        {
            "Rating": label,
            "Average score": round(sum(getattr(record, field) for record in overall) / len(overall), 2),
        }
        for label, field in rating_fields
        if overall
    ]
    score_distribution = [
        {
            "Score": score,
            "Rating": label,
            "Responses": sum(getattr(record, field) == score for record in overall),
        }
        for label, field in rating_fields
        for score in range(1, 6)
    ]

    return {
        "daily_submissions": [
            {"Date": day, "Feedback type": source, "Submissions": count}
            for (day, source), count in sorted(daily_counts.items())
        ],
        "helpful_rates": helpful_rates,
        "thumb_counts": [
            {"Experience": source, "Rating": rating, "Responses": count}
            for (source, rating), count in sorted(source_counts.items())
        ],
        "rating_averages": rating_averages,
        "score_distribution": score_distribution,
    }


def _feedback_day(created_at: str) -> str:
    """Return a stable UTC day even when historical rows omit an offset."""

    try:
        return datetime.fromisoformat(created_at.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return created_at[:10]
