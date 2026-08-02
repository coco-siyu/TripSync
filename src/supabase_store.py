"""Small server-side Supabase adapter used by TripSync persistence modules."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any


def is_configured() -> bool:
    """Return whether server-only Supabase credentials are available."""

    return bool(
        os.getenv("SUPABASE_URL", "").strip()
        and os.getenv("SUPABASE_SECRET_KEY", "").strip()
    )


@lru_cache(maxsize=1)
def client() -> Any:
    """Create one cached server-side client; never expose it to the browser."""

    if not is_configured():
        raise RuntimeError("Supabase is not configured")
    from supabase import create_client

    return create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"]
    )


def upsert(table: str, row: dict[str, Any], *, conflict: str) -> None:
    client().table(table).upsert(row, on_conflict=conflict).execute()


def select(table: str, *, order: str) -> list[dict[str, Any]]:
    response = client().table(table).select("*").order(order, desc=True).execute()
    return list(response.data)


def select_for_session(table: str, session_id: str) -> list[dict[str, Any]]:
    response = (
        client()
        .table(table)
        .select("*")
        .eq("session_id", session_id)
        .order("updated_at", desc=True)
        .execute()
    )
    return list(response.data)
