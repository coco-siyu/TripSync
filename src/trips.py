"""Private saved-trip persistence for Supabase and local SQLite fallback."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Mapping
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from src.feedback import DEFAULT_FEEDBACK_DATABASE_PATH
from src.models import ItineraryDay, ItineraryPlan, TripRequest
from src.planner import PACE_RULES, TRANSITION_HOURS
from src.supabase_store import (
    is_configured,
    insert_authenticated,
    rpc_authenticated,
    select_authenticated,
    select_for_session,
    update_authenticated,
    upsert,
)


_ITINERARY_STATE_KEYS = (
    "selected_activity_ids",
    "dismissed_must_do_ids",
    "auto_select_must_dos",
    "itinerary_plan",
    "rejected_activities",
    "itinerary_narrative",
)
_ANONYMOUS_SESSION_PATTERN = re.compile(r"^[a-f0-9]{32}$")


@dataclass(frozen=True)
class SavedTrip:
    trip_id: str
    title: str
    trip: TripRequest
    state: dict
    updated_at: str
    owner_id: str | None = None


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
    if "auto_select_must_dos" not in version:
        restored["auto_select_must_dos"] = True
    restored["active_itinerary_version_id"] = version["version_id"]
    return restored


def revise_itinerary_plan(
    plan: ItineraryPlan,
    *,
    remove_activity_ids: Iterable[str] = (),
    target_day_by_activity_id: Mapping[str, int] | None = None,
    allow_pace_override: bool = False,
) -> ItineraryPlan:
    """Return a revised itinerary without changing its saved source snapshot."""
    removed_ids = set(remove_activity_ids)
    target_days = dict(target_day_by_activity_id or {})
    known_ids = {
        activity.activity_id
        for day in plan.days
        for activity in day.activities
    }
    unknown_ids = (removed_ids | set(target_days)) - known_ids
    if unknown_ids:
        raise ValueError("Cannot update activity IDs that are not in this itinerary.")

    valid_day_numbers = {day.day_number for day in plan.days}
    invalid_day_numbers = set(target_days.values()) - valid_day_numbers
    if invalid_day_numbers:
        raise ValueError("Choose one of the existing itinerary days for every moved stop.")

    activities_by_day = {day.day_number: [] for day in plan.days}
    for original_day in plan.days:
        for activity in original_day.activities:
            if activity.activity_id in removed_ids:
                continue
            target_day = target_days.get(activity.activity_id, original_day.day_number)
            activities_by_day[target_day].append(activity)

    revised_days = []
    pace_rule = PACE_RULES[plan.pace]
    for original_day in plan.days:
        activities = activities_by_day[original_day.day_number]
        activity_hours = round(sum(activity.duration_hours for activity in activities), 2)
        transition_hours = round(TRANSITION_HOURS * max(0, len(activities) - 1), 2)
        planned_hours = round(activity_hours + transition_hours, 2)
        exceeds_capacity = planned_hours > original_day.capacity_hours
        exceeds_activity_limit = len(activities) > pace_rule.max_activities
        override_approved = original_day.pace_override_approved or allow_pace_override
        if (exceeds_capacity or exceeds_activity_limit) and not override_approved:
            pace_issues = []
            if exceeds_activity_limit:
                pace_issues.append(
                    f"has more than {pace_rule.max_activities} activities for a "
                    f"{plan.pace.value} pace"
                )
            if exceeds_capacity:
                pace_issues.append(
                    f"would be {planned_hours:g} hours, over its "
                    f"{original_day.capacity_hours:g}-hour capacity"
                )
            raise ValueError(
                f"Day {original_day.day_number} {' and '.join(pace_issues)}. "
                "Confirm the pace override before saving."
            )
        revised_days.append(
            ItineraryDay(
                day_number=original_day.day_number,
                activities=activities,
                activity_hours=activity_hours,
                transition_hours=transition_hours,
                planned_hours=planned_hours,
                capacity_hours=original_day.capacity_hours,
                pace_override_approved=(
                    exceeds_capacity or exceeds_activity_limit
                )
                and override_approved,
            )
        )

    return ItineraryPlan.model_validate(
        {
            **plan.model_dump(mode="json"),
            "days": [day.model_dump(mode="json") for day in revised_days],
        }
    )


def _snapshot_itinerary(
    state: dict, position: int, *, label: str | None = None
) -> dict:
    return {
        "version_id": uuid4().hex,
        "label": (label or "").strip() or f"Itinerary {position}",
        "saved_at": datetime.now(UTC).isoformat(),
        **{
            key: state.get(key)
            for key in _ITINERARY_STATE_KEYS
            if key in state
        },
    }


def _existing_trip_state(
    trip_id: str,
    session_id: str,
    *,
    auth_access_token: str | None = None,
) -> tuple[dict, str | None, str | None]:
    for record in list_saved_trips(
        session_id,
        auth_access_token=auth_access_token,
    ):
        if record.trip_id == trip_id:
            return record.state, record.updated_at, record.owner_id
    return {}, None, None


def claim_anonymous_trips(
    anonymous_session_id: str,
    access_token: str,
) -> int:
    """Atomically move anonymous remote trips to the authenticated user."""

    normalized_session_id = anonymous_session_id.strip()
    if not _ANONYMOUS_SESSION_PATTERN.fullmatch(normalized_session_id):
        raise ValueError("The anonymous browser session is not valid")
    result = rpc_authenticated(
        "claim_anonymous_trips",
        {"anonymous_session_id": normalized_session_id},
        access_token,
    )
    try:
        claimed_count = int(result)
    except (TypeError, ValueError) as error:
        raise RuntimeError("Supabase returned an invalid trip transfer result") from error
    if claimed_count < 0:
        raise RuntimeError("Supabase returned an invalid trip transfer result")
    return claimed_count


def _ensure_schema(
    connection: sqlite3.Connection,
    migration_session_id: str,
) -> None:
    connection.execute("""CREATE TABLE IF NOT EXISTS saved_trips (
        trip_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, title TEXT NOT NULL, trip_json TEXT NOT NULL,
        state_json TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(saved_trips)").fetchall()
    }
    if "session_id" not in columns:
        connection.execute("ALTER TABLE saved_trips ADD COLUMN session_id TEXT")
    connection.execute(
        """UPDATE saved_trips SET session_id = ?
        WHERE session_id IS NULL OR trim(session_id) IN ('', 'legacy')""",
        (migration_session_id,),
    )


def save_trip(
    trip: TripRequest,
    state: dict,
    *,
    title: str | None = None,
    trip_id: str | None = None,
    session_id: str = "local",
    save_itinerary_version: bool = False,
    itinerary_label: str | None = None,
    force_new_itinerary_version: bool = False,
    auth_access_token: str | None = None,
    owner_id: str | None = None,
) -> SavedTrip:
    """Save a validated trip and its serializable planner state."""

    resolved_trip_id = trip_id or uuid4().hex
    previous_state, previous_updated_at, existing_owner_id = _existing_trip_state(
        resolved_trip_id,
        session_id,
        auth_access_token=auth_access_token,
    )
    resolved_owner_id = (
        existing_owner_id
        or (session_id if auth_access_token else owner_id)
        or session_id
    )
    stored_state = dict(state)
    if save_itinerary_version and state.get("itinerary_plan"):
        versions = itinerary_versions(
            previous_state, fallback_updated_at=previous_updated_at
        )
        snapshot = _snapshot_itinerary(
            state, len(versions) + 1, label=itinerary_label
        )
        matching_version = None
        if not force_new_itinerary_version:
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
        resolved_owner_id,
    )
    if is_configured():
        row = {
            "session_id": resolved_owner_id,
            "trip_id": record.trip_id,
            "title": record.title,
            "trip_json": trip.model_dump(mode="json"),
            "state_json": stored_state,
            "updated_at": record.updated_at,
        }
        if auth_access_token:
            if existing_owner_id:
                update_authenticated(
                    "saved_trips",
                    {
                        key: value
                        for key, value in row.items()
                        if key not in {"session_id", "trip_id"}
                    },
                    auth_access_token,
                    filters={"trip_id": record.trip_id},
                )
            else:
                insert_authenticated(
                    "saved_trips",
                    row,
                    auth_access_token,
                )
        else:
            upsert(
                "saved_trips",
                row,
                conflict="session_id,trip_id",
            )
        return record
    with closing(sqlite3.connect(DEFAULT_FEEDBACK_DATABASE_PATH)) as connection:
        with connection:
            _ensure_schema(connection, session_id)
            connection.execute("""INSERT INTO saved_trips (
                    trip_id, session_id, title, trip_json, state_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(trip_id) DO UPDATE SET session_id=excluded.session_id,
                title=excluded.title, trip_json=excluded.trip_json,
                state_json=excluded.state_json, updated_at=excluded.updated_at""",
                (record.trip_id, session_id, record.title, json.dumps(trip.model_dump(mode="json")), json.dumps(stored_state), record.updated_at))
    return record


def list_saved_trips(
    session_id: str = "local",
    *,
    auth_access_token: str | None = None,
) -> list[SavedTrip]:
    if is_configured():
        rows = (
            select_authenticated(
                "saved_trips",
                auth_access_token,
                order="updated_at",
            )
            if auth_access_token
            else select_for_session("saved_trips", session_id)
        )
        return [
            SavedTrip(
                row["trip_id"],
                row["title"],
                TripRequest.model_validate(row["trip_json"]),
                row["state_json"],
                row["updated_at"],
                row.get("session_id"),
            )
            for row in rows
        ]
    if not DEFAULT_FEEDBACK_DATABASE_PATH.exists():
        return []
    with closing(sqlite3.connect(DEFAULT_FEEDBACK_DATABASE_PATH)) as connection:
        with connection:
            _ensure_schema(connection, session_id)
            rows = connection.execute(
                """SELECT trip_id, title, trip_json, state_json, updated_at
                FROM saved_trips WHERE session_id = ? ORDER BY updated_at DESC""",
                (session_id,),
            ).fetchall()
    return [
        SavedTrip(
            row[0],
            row[1],
            TripRequest.model_validate_json(row[2]),
            json.loads(row[3]),
            row[4],
            session_id,
        )
        for row in rows
    ]
