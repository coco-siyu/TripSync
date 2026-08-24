"""Dormant Phase 2 invitation helpers; not wired into the Phase 1 app."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.supabase_store import (
    delete_authenticated,
    insert_authenticated,
    rpc_authenticated,
)


@dataclass(frozen=True)
class TripInvitation:
    token: str
    expires_at: str


def invitation_token_hash(token: str) -> str:
    """Return the one-way representation stored in the database."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def build_invitation_url(base_url: str, token: str) -> str:
    """Add an invitation capability to a deployment URL."""

    normalized_base = base_url.strip().rstrip("/")
    if not normalized_base:
        raise ValueError("A public TripSync URL is required")
    parts = urlsplit(normalized_base)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("The public TripSync URL must use http or https")
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["invite"] = token
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path or "/", urlencode(query), "")
    )


def create_trip_invitation(
    trip_id: str,
    owner_id: str,
    access_token: str,
    *,
    valid_days: int = 7,
    now: datetime | None = None,
) -> TripInvitation:
    """Create a reusable editor invitation while storing only its hash."""

    if not trip_id.strip() or not owner_id.strip() or not access_token.strip():
        raise ValueError("A trip owner and authenticated session are required")
    if not 1 <= valid_days <= 30:
        raise ValueError("Invitation validity must be between 1 and 30 days")
    token = secrets.token_urlsafe(32)
    expires_at = (now or datetime.now(UTC)) + timedelta(days=valid_days)
    insert_authenticated(
        "trip_invitations",
        {
            "token_hash": invitation_token_hash(token),
            "trip_id": trip_id,
            "owner_id": owner_id,
            "expires_at": expires_at.isoformat(),
        },
        access_token,
    )
    return TripInvitation(token=token, expires_at=expires_at.isoformat())


def claim_trip_invitation(token: str, access_token: str) -> str:
    """Join the invited trip through the database's checked claim function."""

    normalized_token = token.strip()
    if len(normalized_token) < 32 or not access_token.strip():
        raise ValueError("This invitation link is not valid")
    trip_id = rpc_authenticated(
        "claim_trip_invitation",
        {"invite_token": normalized_token},
        access_token,
    )
    if not isinstance(trip_id, str) or not trip_id:
        raise RuntimeError("The invitation could not be claimed")
    return trip_id


def revoke_trip_invitations(trip_id: str, access_token: str) -> None:
    """Disable every outstanding link for an owned trip."""

    delete_authenticated(
        "trip_invitations",
        access_token,
        filters={"trip_id": trip_id},
    )
