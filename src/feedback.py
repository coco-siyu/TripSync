"""Local, privacy-conscious persistence for LLM-output feedback."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from src.supabase_store import is_configured, select, upsert


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_DIRECTORY = Path(
    os.getenv("TRIPSYNC_STATE_DIR", str(REPOSITORY_ROOT / "data"))
)
DEFAULT_FEEDBACK_DATABASE_PATH = DEFAULT_STATE_DIRECTORY / "tripsync_feedback.db"

FeedbackTargetType = Literal[
    "trip_story",
    "adjustment_proposal",
    "overall_experience",
]
FeedbackRating = Literal["up", "down"]


@dataclass(frozen=True)
class FeedbackRecord:
    """One organizer's local rating of one generated LLM output."""

    session_id: str
    target_type: FeedbackTargetType
    target_id: str
    rating: FeedbackRating
    comment: str | None
    created_at: str


@dataclass(frozen=True)
class OverallExperienceRecord:
    """One organizer's local three-part rating of a complete itinerary."""

    session_id: str
    itinerary_id: str
    helpfulness: int
    clarity: int
    group_fit: int
    comment: str | None
    created_at: str


def feedback_target_id(target_type: FeedbackTargetType, payload: object) -> str:
    """Return a stable anonymous ID without persisting the generated content."""

    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"{target_type}_{digest[:16]}"


def _connect(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(database_path)


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_feedback (
            session_id TEXT NOT NULL,
            target_type TEXT NOT NULL CHECK (
                target_type IN ('trip_story', 'adjustment_proposal')
            ),
            target_id TEXT NOT NULL,
            rating TEXT NOT NULL CHECK (rating IN ('up', 'down')),
            comment TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (session_id, target_type, target_id)
        )
        """
    )


def record_feedback(
    *,
    session_id: str,
    target_type: FeedbackTargetType,
    target_id: str,
    rating: FeedbackRating,
    comment: str | None = None,
    database_path: Path = DEFAULT_FEEDBACK_DATABASE_PATH,
) -> FeedbackRecord:
    """Save or update one local feedback response using parameterized SQL."""

    if not session_id.strip():
        raise ValueError("session_id cannot be blank")
    if rating not in {"up", "down"}:
        raise ValueError("rating must be 'up' or 'down'")
    cleaned_comment = comment.strip() if comment and comment.strip() else None
    if cleaned_comment and len(cleaned_comment) > 1_000:
        raise ValueError("feedback comments must be at most 1000 characters")
    created_at = datetime.now(UTC).isoformat()
    record = FeedbackRecord(
        session_id=session_id.strip(),
        target_type=target_type,
        target_id=target_id,
        rating=rating,
        comment=cleaned_comment,
        created_at=created_at,
    )
    if is_configured():
        upsert(
            "llm_feedback",
            {
                "session_id": record.session_id,
                "target_type": record.target_type,
                "target_id": record.target_id,
                "rating": record.rating,
                "comment": record.comment,
                "created_at": record.created_at,
            },
            conflict="session_id,target_type,target_id",
        )
        return record
    with closing(_connect(database_path)) as connection:
        with connection:
            _ensure_schema(connection)
            connection.execute(
                """
                INSERT INTO llm_feedback (
                    session_id, target_type, target_id, rating, comment, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, target_type, target_id) DO UPDATE SET
                    rating = excluded.rating,
                    comment = excluded.comment,
                    created_at = excluded.created_at
                """,
                (
                    record.session_id,
                    record.target_type,
                    record.target_id,
                    record.rating,
                    record.comment,
                    record.created_at,
                ),
            )
    return record


def list_feedback(
    database_path: Path = DEFAULT_FEEDBACK_DATABASE_PATH,
) -> list[FeedbackRecord]:
    """Return local feedback rows for future human-review analysis."""

    if is_configured():
        return [
            FeedbackRecord(
                row["session_id"], row["target_type"], row["target_id"],
                row["rating"], row.get("comment"), row["created_at"],
            )
            for row in select("llm_feedback", order="created_at")
        ]
    if not database_path.exists():
        return []
    with closing(_connect(database_path)) as connection:
        _ensure_schema(connection)
        rows = connection.execute(
            """
            SELECT session_id, target_type, target_id, rating, comment, created_at
            FROM llm_feedback
            ORDER BY created_at DESC
            """
        ).fetchall()
    return [FeedbackRecord(*row) for row in rows]


def record_overall_experience_feedback(
    *,
    session_id: str,
    itinerary_id: str,
    helpfulness: int,
    clarity: int,
    group_fit: int,
    comment: str | None = None,
    database_path: Path = DEFAULT_FEEDBACK_DATABASE_PATH,
) -> OverallExperienceRecord:
    """Save or update the overall experience rubric for one itinerary."""

    if not session_id.strip():
        raise ValueError("session_id cannot be blank")
    scores = (helpfulness, clarity, group_fit)
    if any(not isinstance(score, int) or not 1 <= score <= 5 for score in scores):
        raise ValueError("overall feedback scores must be whole numbers from 1 to 5")
    cleaned_comment = comment.strip() if comment and comment.strip() else None
    if cleaned_comment and len(cleaned_comment) > 1_000:
        raise ValueError("feedback comments must be at most 1000 characters")
    record = OverallExperienceRecord(
        session_id=session_id.strip(),
        itinerary_id=itinerary_id,
        helpfulness=helpfulness,
        clarity=clarity,
        group_fit=group_fit,
        comment=cleaned_comment,
        created_at=datetime.now(UTC).isoformat(),
    )
    if is_configured():
        upsert(
            "overall_experience_feedback",
            {
                "session_id": record.session_id,
                "itinerary_id": record.itinerary_id,
                "helpfulness": record.helpfulness,
                "clarity": record.clarity,
                "group_fit": record.group_fit,
                "comment": record.comment,
                "created_at": record.created_at,
            },
            conflict="session_id,itinerary_id",
        )
        return record
    with closing(_connect(database_path)) as connection:
        with connection:
            _ensure_overall_experience_schema(connection)
            connection.execute(
                """
                INSERT INTO overall_experience_feedback (
                    session_id, itinerary_id, helpfulness, clarity, group_fit,
                    comment, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, itinerary_id) DO UPDATE SET
                    helpfulness = excluded.helpfulness,
                    clarity = excluded.clarity,
                    group_fit = excluded.group_fit,
                    comment = excluded.comment,
                    created_at = excluded.created_at
                """,
                (
                    record.session_id,
                    record.itinerary_id,
                    record.helpfulness,
                    record.clarity,
                    record.group_fit,
                    record.comment,
                    record.created_at,
                ),
            )
    return record


def _ensure_overall_experience_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS overall_experience_feedback (
            session_id TEXT NOT NULL,
            itinerary_id TEXT NOT NULL,
            helpfulness INTEGER NOT NULL CHECK (helpfulness BETWEEN 1 AND 5),
            clarity INTEGER NOT NULL CHECK (clarity BETWEEN 1 AND 5),
            group_fit INTEGER NOT NULL CHECK (group_fit BETWEEN 1 AND 5),
            comment TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (session_id, itinerary_id)
        )
        """
    )


def list_overall_experience_feedback(
    database_path: Path = DEFAULT_FEEDBACK_DATABASE_PATH,
) -> list[OverallExperienceRecord]:
    """Return stored overall experience ratings for human-review analysis."""

    if is_configured():
        return [
            OverallExperienceRecord(
                row["session_id"], row["itinerary_id"], row["helpfulness"],
                row["clarity"], row["group_fit"], row.get("comment"),
                row["created_at"],
            )
            for row in select("overall_experience_feedback", order="created_at")
        ]
    if not database_path.exists():
        return []
    with closing(_connect(database_path)) as connection:
        _ensure_overall_experience_schema(connection)
        rows = connection.execute(
            """
            SELECT session_id, itinerary_id, helpfulness, clarity, group_fit,
                   comment, created_at
            FROM overall_experience_feedback
            ORDER BY created_at DESC
            """
        ).fetchall()
    return [OverallExperienceRecord(*row) for row in rows]
