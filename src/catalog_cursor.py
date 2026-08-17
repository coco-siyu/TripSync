"""Durable, queue-aware cursor for scheduled catalog ingestion."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from src.destination_queue import DestinationQueueItem
from src.supabase_store import insert, select, upsert


CURSOR_TABLE = "catalog_ingestion_state"
RUNS_TABLE = "catalog_ingestion_runs"
CURSOR_ID = "weekly_catalog"


def destination_key(destination: DestinationQueueItem) -> str:
    """Return a stable, case-insensitive key for an entry in the queue."""

    return f"{destination.city.casefold()}|{destination.country.casefold()}"


def select_from_cursor(
    destinations: list[DestinationQueueItem],
    batch_size: int,
    next_destination_key: str,
) -> list[DestinationQueueItem]:
    """Select one wrapping batch beginning at the stored destination key."""

    if not destinations:
        raise ValueError("at least one destination is required")
    if batch_size < 1 or batch_size >= len(destinations):
        return destinations
    try:
        start = [destination_key(item) for item in destinations].index(next_destination_key)
    except ValueError:
        # A removed/paused city must not stall the schedule forever.
        start = 0
    return [destinations[(start + offset) % len(destinations)] for offset in range(batch_size)]


def next_destination_after(
    destinations: list[DestinationQueueItem],
    destination: DestinationQueueItem,
) -> str:
    """Return the key immediately after ``destination``, wrapping safely."""

    keys = [destination_key(item) for item in destinations]
    try:
        index = keys.index(destination_key(destination))
    except ValueError:
        return keys[0]
    return keys[(index + 1) % len(keys)]


def read_or_initialize_cursor(initial_destination: DestinationQueueItem) -> str:
    """Read the shared cursor, creating it once with the queue's chosen start."""

    rows = select(CURSOR_TABLE, order="updated_at")
    for row in rows:
        if row.get("cursor_id") == CURSOR_ID:
            return str(row["next_destination_key"])
    key = destination_key(initial_destination)
    upsert(
        CURSOR_TABLE,
        {
            "cursor_id": CURSOR_ID,
            "next_destination_key": key,
            "updated_at": datetime.now(UTC).isoformat(),
        },
        conflict="cursor_id",
    )
    return key


def advance_cursor(next_destination_key: str) -> None:
    """Persist the next starting point only after a batch has finished."""

    upsert(
        CURSOR_TABLE,
        {
            "cursor_id": CURSOR_ID,
            "next_destination_key": next_destination_key,
            "updated_at": datetime.now(UTC).isoformat(),
        },
        conflict="cursor_id",
    )


def record_run(
    destination: DestinationQueueItem,
    *,
    status: str,
    published_count: int | None = None,
    error_message: str | None = None,
    run_id: str | None = None,
) -> None:
    """Append a small audit record for one attempted destination."""

    if status not in {"succeeded", "failed"}:
        raise ValueError("status must be succeeded or failed")
    insert(
        RUNS_TABLE,
        {
            "run_id": run_id or str(uuid4()),
            "destination_key": destination_key(destination),
            "city": destination.city,
            "country": destination.country,
            "status": status,
            "published_count": published_count,
            "error_message": error_message,
            "completed_at": datetime.now(UTC).isoformat(),
        },
    )
