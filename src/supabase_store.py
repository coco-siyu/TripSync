"""Small server-side Supabase adapter used by TripSync persistence modules."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any


def public_key() -> str:
    """Return the browser-safe key used for Supabase Auth and RLS requests."""

    return (
        os.getenv("SUPABASE_PUBLISHABLE_KEY", "").strip()
        or os.getenv("SUPABASE_ANON_KEY", "").strip()
    )


def auth_is_configured() -> bool:
    """Return whether Supabase Auth can be used by this deployment."""

    return bool(os.getenv("SUPABASE_URL", "").strip() and public_key())


def is_configured() -> bool:
    """Return whether server-only Supabase credentials are available."""

    return bool(
        os.getenv("SUPABASE_URL", "").strip()
        and os.getenv("SUPABASE_SECRET_KEY", "").strip()
    )


def account_feature_is_configured() -> bool:
    """Return whether Auth and the remote trip store are both available."""

    return auth_is_configured() and is_configured()


@lru_cache(maxsize=1)
def client() -> Any:
    """Create one cached server-side client; never expose it to the browser."""

    if not is_configured():
        raise RuntimeError("Supabase is not configured")
    from supabase import create_client

    return create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"]
    )


def public_client() -> Any:
    """Create an isolated public client without cross-user session storage."""

    if not auth_is_configured():
        raise RuntimeError("Supabase Auth is not configured")
    from supabase import create_client
    from supabase.lib.client_options import SyncClientOptions

    return create_client(
        os.environ["SUPABASE_URL"],
        public_key(),
        options=SyncClientOptions(
            auto_refresh_token=False,
            persist_session=False,
        ),
    )


def authenticated_client(access_token: str) -> Any:
    """Create an RLS-bound client for one verified user access token."""

    if not access_token.strip():
        raise ValueError("An access token is required")
    active_client = public_client()
    active_client.postgrest.auth(access_token.strip())
    return active_client


def upsert(table: str, row: dict[str, Any], *, conflict: str) -> None:
    client().table(table).upsert(row, on_conflict=conflict).execute()


def upsert_many(table: str, rows: list[dict[str, Any]], *, conflict: str) -> None:
    """Upsert a small batch of server-side records in one request."""

    if rows:
        client().table(table).upsert(rows, on_conflict=conflict).execute()


def insert(table: str, row: dict[str, Any]) -> None:
    """Append one server-side audit record."""

    client().table(table).insert(row).execute()


def select(
    table: str,
    *,
    order: str,
    columns: str = "*",
    desc: bool = True,
) -> list[dict[str, Any]]:
    response = (
        client()
        .table(table)
        .select(columns)
        .order(order, desc=desc)
        .execute()
    )
    return list(response.data)


def delete_in(table: str, column: str, values: list[str]) -> None:
    """Delete known record IDs from a server-side table."""

    if values:
        client().table(table).delete().in_(column, values).execute()


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


def select_authenticated(
    table: str,
    access_token: str,
    *,
    order: str,
    desc: bool = True,
) -> list[dict[str, Any]]:
    """Select rows visible to the signed-in user through database RLS."""

    response = (
        authenticated_client(access_token)
        .table(table)
        .select("*")
        .order(order, desc=desc)
        .execute()
    )
    return list(response.data)


def insert_authenticated(
    table: str,
    row: dict[str, Any],
    access_token: str,
) -> None:
    authenticated_client(access_token).table(table).insert(row).execute()


def update_authenticated(
    table: str,
    row: dict[str, Any],
    access_token: str,
    *,
    filters: dict[str, str],
) -> None:
    query = authenticated_client(access_token).table(table).update(row)
    for column, value in filters.items():
        query = query.eq(column, value)
    query.execute()


def delete_authenticated(
    table: str,
    access_token: str,
    *,
    filters: dict[str, str],
) -> None:
    query = authenticated_client(access_token).table(table).delete()
    for column, value in filters.items():
        query = query.eq(column, value)
    query.execute()


def rpc_authenticated(
    function_name: str,
    params: dict[str, Any],
    access_token: str,
) -> Any:
    """Call an RLS-aware database function as the signed-in user."""

    return authenticated_client(access_token).rpc(function_name, params).execute().data
