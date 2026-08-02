"""Local SQLite persistence for saved TripSync plans."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from src.feedback import DEFAULT_FEEDBACK_DATABASE_PATH
from src.models import TripRequest
from src.supabase_store import is_configured, select_for_session, upsert


@dataclass(frozen=True)
class SavedTrip:
    trip_id: str
    title: str
    trip: TripRequest
    state: dict
    updated_at: str


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute("""CREATE TABLE IF NOT EXISTS saved_trips (
        trip_id TEXT PRIMARY KEY, title TEXT NOT NULL, trip_json TEXT NOT NULL,
        state_json TEXT NOT NULL, updated_at TEXT NOT NULL)""")


def save_trip(trip: TripRequest, state: dict, *, title: str | None = None, trip_id: str | None = None, session_id: str = "local") -> SavedTrip:
    """Save a validated trip and its serializable planner state."""

    record = SavedTrip(trip_id or uuid4().hex, (title or f"{trip.destination} · {trip.days} days").strip(), trip, state, datetime.now(UTC).isoformat())
    if is_configured():
        upsert("saved_trips", {"session_id": session_id, "trip_id": record.trip_id, "title": record.title, "trip_json": trip.model_dump(mode="json"), "state_json": state, "updated_at": record.updated_at}, conflict="session_id,trip_id")
        return record
    with closing(sqlite3.connect(DEFAULT_FEEDBACK_DATABASE_PATH)) as connection:
        with connection:
            _ensure_schema(connection)
            connection.execute("""INSERT INTO saved_trips VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(trip_id) DO UPDATE SET title=excluded.title, trip_json=excluded.trip_json,
                state_json=excluded.state_json, updated_at=excluded.updated_at""",
                (record.trip_id, record.title, json.dumps(trip.model_dump(mode="json")), json.dumps(state), record.updated_at))
    return record


def list_saved_trips(session_id: str = "local") -> list[SavedTrip]:
    if is_configured():
        rows = select_for_session("saved_trips", session_id)
        return [SavedTrip(row["trip_id"], row["title"], TripRequest.model_validate(row["trip_json"]), row["state_json"], row["updated_at"]) for row in rows]
    if not DEFAULT_FEEDBACK_DATABASE_PATH.exists():
        return []
    with closing(sqlite3.connect(DEFAULT_FEEDBACK_DATABASE_PATH)) as connection:
        _ensure_schema(connection)
        rows = connection.execute("SELECT trip_id, title, trip_json, state_json, updated_at FROM saved_trips ORDER BY updated_at DESC").fetchall()
    return [SavedTrip(row[0], row[1], TripRequest.model_validate_json(row[2]), json.loads(row[3]), row[4]) for row in rows]
