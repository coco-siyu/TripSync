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


_ITINERARY_STATE_KEYS = (
    "selected_activity_ids",
    "dismissed_must_do_ids",
    "itinerary_plan",
    "rejected_activities",
    "itinerary_narrative",
)


@dataclass(frozen=True)
class SavedTrip:
    trip_id: str
    title: str
    trip: TripRequest
    state: dict
    updated_at: str


def itinerary_versions(
    state: dict,
    *,
    fallback_updated_at: str | None = None,
) -> list[dict]:
    """Return saved itinerary snapshots, including a readable legacy snapshot."""

    versions = [
        dict(version)
        for version in state.get("itinerary_versions", [])
        if isinstance(version, dict) and version.get("version_id")
    ]
    if versions or not state.get("itinerary_plan"):
        return versions
    return [
        {
            "version_id": "legacy",
            "label": "Saved itinerary",
            "saved_at": fallback_updated_at,
            **{
                key: state.get(key)
                for key in _ITINERARY_STATE_KEYS
                if key in state
            },
        }
    ]


def state_for_itinerary_version(
    state: dict,
    version_id: str | None = None,
    *,
    fallback_updated_at: str | None = None,
) -> dict:
    """Restore planner state for one saved itinerary without losing trip data."""

    versions = itinerary_versions(
        state, fallback_updated_at=fallback_updated_at
    )
    if not versions:
        return dict(state)
    version = next(
        (
            item
            for item in versions
            if item["version_id"] == version_id
        ),
        versions[-1],
    )
    restored = dict(state)
    restored.update(
        {key: version.get(key) for key in _ITINERARY_STATE_KEYS if key in version}
    )
    restored["active_itinerary_version_id"] = version["version_id"]
    return restored


def _snapshot_itinerary(state: dict, position: int) -> dict:
    return {
        "version_id": uuid4().hex,
        "label": f"Itinerary {position}",
        "saved_at": datetime.now(UTC).isoformat(),
        **{
            key: state.get(key)
            for key in _ITINERARY_STATE_KEYS
            if key in state
        },
    }


def _existing_trip_state(trip_id: str, session_id: str) -> tuple[dict, str | None]:
    for record in list_saved_trips(session_id):
        if record.trip_id == trip_id:
            return record.state, record.updated_at
    return {}, None


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute("""CREATE TABLE IF NOT EXISTS saved_trips (
        trip_id TEXT PRIMARY KEY, title TEXT NOT NULL, trip_json TEXT NOT NULL,
        state_json TEXT NOT NULL, updated_at TEXT NOT NULL)""")


def save_trip(
    trip: TripRequest,
    state: dict,
    *,
    title: str | None = None,
    trip_id: str | None = None,
    session_id: str = "local",
    save_itinerary_version: bool = False,
) -> SavedTrip:
    """Save a validated trip and its serializable planner state."""

    resolved_trip_id = trip_id or uuid4().hex
    previous_state, previous_updated_at = _existing_trip_state(
        resolved_trip_id, session_id
    )
    stored_state = dict(state)
    if save_itinerary_version and state.get("itinerary_plan"):
        versions = itinerary_versions(
            previous_state, fallback_updated_at=previous_updated_at
        )
        snapshot = _snapshot_itinerary(state, len(versions) + 1)
        matching_version = next(
            (
                version
                for version in versions
                if version.get("itinerary_plan") == snapshot.get("itinerary_plan")
            ),
            None,
        )
        if matching_version is None:
            versions.append(snapshot)
            matching_version = snapshot
        stored_state["itinerary_versions"] = versions
        stored_state["active_itinerary_version_id"] = matching_version[
            "version_id"
        ]
    elif previous_state.get("itinerary_versions"):
        stored_state["itinerary_versions"] = previous_state["itinerary_versions"]
        stored_state["active_itinerary_version_id"] = previous_state.get(
            "active_itinerary_version_id"
        )

    record = SavedTrip(
        resolved_trip_id,
        (title or f"{trip.destination} · {trip.days} days").strip(),
        trip,
        stored_state,
        datetime.now(UTC).isoformat(),
    )
    if is_configured():
        upsert("saved_trips", {"session_id": session_id, "trip_id": record.trip_id, "title": record.title, "trip_json": trip.model_dump(mode="json"), "state_json": stored_state, "updated_at": record.updated_at}, conflict="session_id,trip_id")
        return record
    with closing(sqlite3.connect(DEFAULT_FEEDBACK_DATABASE_PATH)) as connection:
        with connection:
            _ensure_schema(connection)
            connection.execute("""INSERT INTO saved_trips VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(trip_id) DO UPDATE SET title=excluded.title, trip_json=excluded.trip_json,
                state_json=excluded.state_json, updated_at=excluded.updated_at""",
                (record.trip_id, record.title, json.dumps(trip.model_dump(mode="json")), json.dumps(stored_state), record.updated_at))
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
