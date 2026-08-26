"""Secure trip-sharing invitation helpers with explicit access roles."""

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
    access_role: str = "viewer"


@dataclass(frozen=True)
class SharedTripIdentity:
    """Globally identify a trip inside its owner's namespace."""

    owner_id: str
    trip_id: str
    access_role: str = "viewer"


_SHARED_ACCESS_ROLES = {"viewer", "collaborator"}


def invitation_token_hash(token: str) -> str:
    """Return the one-way representation stored in the database."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def build_invitation_url(base_url: str, token: str) -> str:
    """Add an invitation capability to a deployment URL."""

    normalized_base = base_url.strip().rstrip("/")
    if not normalized_base:
        raise ValueError("A public TripSync URL is required")
    parts = urlsplit(normalized_base)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.netloc
        or parts.username
        or parts.password
    ):
        raise ValueError("The public TripSync URL must use http or https")
    normalized_token = token.strip()
    if len(normalized_token) < 32 or len(normalized_token) > 256:
        raise ValueError("This invitation link is not valid")
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["invite"] = normalized_token
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path or "/", urlencode(query), "")
    )


def create_trip_invitation(
    trip_id: str,
    owner_id: str,
    access_token: str,
    *,
    access_role: str = "viewer",
    valid_days: int = 7,
    now: datetime | None = None,
) -> TripInvitation:
    """Create a reusable viewer invitation while storing only its hash."""

    if not trip_id.strip() or not owner_id.strip() or not access_token.strip():
        raise ValueError("A trip owner and authenticated session are required")
    normalized_role = access_role.strip().casefold()
    if normalized_role not in _SHARED_ACCESS_ROLES:
        raise ValueError("Choose viewer or collaborator access")
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
            "role": normalized_role,
            "expires_at": expires_at.isoformat(),
        },
        access_token,
    )
    return TripInvitation(
        token=token,
        expires_at=expires_at.isoformat(),
        access_role=normalized_role,
    )


def claim_trip_invitation(
    token: str,
    access_token: str,
) -> SharedTripIdentity:
    """Claim viewer access through the database's checked invitation function."""

    normalized_token = token.strip()
    if (
        len(normalized_token) < 32
        or len(normalized_token) > 256
        or not access_token.strip()
    ):
        raise ValueError("This invitation link is not valid")
    result = rpc_authenticated(
        "claim_trip_invitation",
        {"invite_token": normalized_token},
        access_token,
    )
    if not isinstance(result, dict):
        raise RuntimeError("The invitation could not be claimed")
    owner_id = str(result.get("owner_id") or "").strip()
    trip_id = str(result.get("trip_id") or "").strip()
    access_role = str(result.get("role") or "viewer").strip().casefold()
    if not owner_id or not trip_id or access_role not in _SHARED_ACCESS_ROLES:
        raise RuntimeError("The invitation could not be claimed")
    return SharedTripIdentity(
        owner_id=owner_id,
        trip_id=trip_id,
        access_role=access_role,
    )


def revoke_trip_sharing(trip_id: str, access_token: str) -> dict[str, int]:
    """Revoke every viewer and outstanding invitation for an owned trip."""

    if not trip_id.strip() or not access_token.strip():
        raise ValueError("An owned trip and authenticated session are required")
    result = rpc_authenticated(
        "revoke_trip_sharing",
        {"target_trip_id": trip_id},
        access_token,
    )
    if not isinstance(result, dict):
        raise RuntimeError("Trip sharing could not be revoked")
    try:
        return {
            "revoked_links": max(0, int(result.get("revoked_links", 0))),
            "removed_viewers": max(0, int(result.get("removed_viewers", 0))),
        }
    except (TypeError, ValueError) as error:
        raise RuntimeError("Supabase returned an invalid sharing result") from error


def leave_shared_trip(
    identity: SharedTripIdentity,
    member_id: str,
    access_token: str,
) -> None:
    """Remove the current viewer's membership; RLS rejects any other target."""

    if not identity.owner_id or not identity.trip_id or not member_id.strip():
        raise ValueError("A shared trip and signed-in viewer are required")
    delete_authenticated(
        "trip_members",
        access_token,
        filters={
            "owner_id": identity.owner_id,
            "trip_id": identity.trip_id,
            "member_id": member_id,
        },
    )
