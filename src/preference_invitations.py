"""Named, account-bound invitations for collecting traveler preferences."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from src.invitations import invitation_token_hash
from src.models import TravelerProfile, TripBasics, TripRequest
from src.supabase_store import rpc_authenticated, select_authenticated


@dataclass(frozen=True)
class PreferenceInvitation:
    traveler_name: str
    slot_id: str
    token: str
    expires_at: str


@dataclass(frozen=True)
class PreferenceSlot:
    slot_id: str
    traveler_name: str
    position: int
    profile: TravelerProfile | None = None
    member_id: str | None = None

    @property
    def is_complete(self) -> bool:
        return self.profile is not None


@dataclass(frozen=True)
class PreferenceDraft:
    draft_id: str
    owner_id: str
    title: str
    trip: TripBasics
    slots: tuple[PreferenceSlot, ...]
    updated_at: str

    @property
    def is_ready(self) -> bool:
        return len(self.slots) >= 2 and all(slot.is_complete for slot in self.slots)

    def to_trip_request(self) -> TripRequest:
        if not self.is_ready:
            raise ValueError("Every traveler must submit preferences first")
        return TripRequest(
            **self.trip.model_dump(mode="json"),
            travelers=[slot.profile for slot in self.slots if slot.profile is not None],
        )


@dataclass(frozen=True)
class PreferenceDraftCreation:
    draft: PreferenceDraft
    invitations: tuple[PreferenceInvitation, ...]


@dataclass(frozen=True)
class PreferenceAssignment:
    draft_id: str
    slot_id: str
    traveler_name: str
    trip: TripBasics
    profile: TravelerProfile | None = None


def build_preference_invitation_url(base_url: str, token: str) -> str:
    """Add a named-profile capability to a deployment URL."""

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
        raise ValueError("This preference invitation link is not valid")
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["profile_invite"] = normalized_token
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path or "/", urlencode(query), "")
    )


def _normalized_invitee_names(names: list[str], organizer_name: str) -> list[str]:
    normalized = [name.strip() for name in names]
    if not 1 <= len(normalized) <= 5 or any(not name for name in normalized):
        raise ValueError("Name between one and five invited travelers")
    all_names = [organizer_name.strip(), *normalized]
    if len({name.casefold() for name in all_names}) != len(all_names):
        raise ValueError("Traveler names must be unique")
    if any(len(name) > 80 for name in all_names):
        raise ValueError("Traveler names must be 80 characters or fewer")
    return normalized


def create_preference_draft(
    trip: TripBasics | dict[str, Any],
    organizer_profile: TravelerProfile | dict[str, Any],
    invitee_names: list[str],
    owner_id: str,
    access_token: str,
    *,
    valid_days: int = 7,
    now: datetime | None = None,
) -> PreferenceDraftCreation:
    """Create a planning draft and one non-recoverable link per named traveler."""

    if not owner_id.strip() or not access_token.strip():
        raise ValueError("A signed-in organizer is required")
    if not 1 <= valid_days <= 30:
        raise ValueError("Invitation validity must be between 1 and 30 days")
    validated_trip = TripBasics.model_validate(trip)
    organizer = TravelerProfile.model_validate(organizer_profile)
    names = _normalized_invitee_names(invitee_names, organizer.name)
    draft_id = uuid4().hex
    expires_at = (now or datetime.now(UTC)) + timedelta(days=valid_days)

    organizer_slot = PreferenceSlot(
        slot_id=uuid4().hex,
        traveler_name=organizer.name,
        position=0,
        profile=organizer,
        member_id=owner_id,
    )
    invitations: list[PreferenceInvitation] = []
    slot_payloads: list[dict[str, Any]] = [
        {
            "slot_id": organizer_slot.slot_id,
            "traveler_name": organizer.name,
            "position": 0,
            "profile": organizer.model_dump(mode="json"),
            "token_hash": None,
            "expires_at": None,
        }
    ]
    pending_slots: list[PreferenceSlot] = []
    for position, name in enumerate(names, start=1):
        slot_id = uuid4().hex
        token = secrets.token_urlsafe(32)
        invitations.append(
            PreferenceInvitation(name, slot_id, token, expires_at.isoformat())
        )
        pending_slots.append(
            PreferenceSlot(slot_id=slot_id, traveler_name=name, position=position)
        )
        slot_payloads.append(
            {
                "slot_id": slot_id,
                "traveler_name": name,
                "position": position,
                "profile": None,
                "token_hash": invitation_token_hash(token),
                "expires_at": expires_at.isoformat(),
            }
        )

    title = f"{validated_trip.destination} · {validated_trip.days} days"
    rpc_authenticated(
        "create_preference_draft",
        {
            "target_draft_id": draft_id,
            "draft_title": title,
            "trip_payload": validated_trip.model_dump(mode="json"),
            "slot_payloads": slot_payloads,
        },
        access_token,
    )
    return PreferenceDraftCreation(
        draft=PreferenceDraft(
            draft_id=draft_id,
            owner_id=owner_id,
            title=title,
            trip=validated_trip,
            slots=(organizer_slot, *pending_slots),
            updated_at=(now or datetime.now(UTC)).isoformat(),
        ),
        invitations=tuple(invitations),
    )


def create_slot_invitation(
    draft_id: str,
    slot: PreferenceSlot,
    owner_id: str,
    access_token: str,
    *,
    valid_days: int = 7,
    now: datetime | None = None,
) -> PreferenceInvitation:
    """Issue a fresh link for an unclaimed slot; old links can be deleted by RPC."""

    if not draft_id.strip() or not owner_id.strip() or not access_token.strip():
        raise ValueError("An owned preference draft is required")
    if slot.member_id or slot.profile:
        raise ValueError("This traveler has already joined the draft")
    if not 1 <= valid_days <= 30:
        raise ValueError("Invitation validity must be between 1 and 30 days")
    token = secrets.token_urlsafe(32)
    expires_at = (now or datetime.now(UTC)) + timedelta(days=valid_days)
    rpc_authenticated(
        "replace_preference_invitation",
        {
            "target_draft_id": draft_id,
            "target_slot_id": slot.slot_id,
            "new_token_hash": invitation_token_hash(token),
            "new_expires_at": expires_at.isoformat(),
        },
        access_token,
    )
    return PreferenceInvitation(
        slot.traveler_name,
        slot.slot_id,
        token,
        expires_at.isoformat(),
    )


def claim_preference_invitation(
    token: str,
    access_token: str,
) -> PreferenceAssignment:
    """Bind one named slot to the signed-in account that opened its link."""

    normalized_token = token.strip()
    if (
        len(normalized_token) < 32
        or len(normalized_token) > 256
        or not access_token.strip()
    ):
        raise ValueError("This preference invitation link is not valid")
    result = rpc_authenticated(
        "claim_preference_invitation",
        {"invite_token": normalized_token},
        access_token,
    )
    if not isinstance(result, dict):
        raise RuntimeError("The preference invitation could not be claimed")
    try:
        profile_payload = result.get("profile")
        return PreferenceAssignment(
            draft_id=str(result["draft_id"]),
            slot_id=str(result["slot_id"]),
            traveler_name=str(result["traveler_name"]),
            trip=TripBasics.model_validate(result["trip"]),
            profile=(
                TravelerProfile.model_validate(profile_payload)
                if profile_payload
                else None
            ),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("Supabase returned an invalid preference assignment") from error


def submit_preference_profile(
    assignment: PreferenceAssignment,
    profile: TravelerProfile | dict[str, Any],
    access_token: str,
) -> TravelerProfile:
    """Save the current member's validated profile without exposing other slots."""

    validated = TravelerProfile.model_validate(profile)
    if validated.name.casefold() != assignment.traveler_name.casefold():
        raise ValueError("The traveler name on an invitation cannot be changed")
    rpc_authenticated(
        "submit_preference_profile",
        {
            "target_draft_id": assignment.draft_id,
            "target_slot_id": assignment.slot_id,
            "profile_payload": validated.model_dump(mode="json"),
        },
        access_token,
    )
    return validated


def _slot_from_row(row: dict[str, Any]) -> PreferenceSlot:
    profile_payload = row.get("profile_json")
    return PreferenceSlot(
        slot_id=str(row.get("slot_id") or ""),
        traveler_name=str(row.get("traveler_name") or ""),
        position=int(row.get("position") or 0),
        profile=(
            TravelerProfile.model_validate(profile_payload)
            if profile_payload
            else None
        ),
        member_id=str(row.get("member_id") or "") or None,
    )


def list_preference_drafts(access_token: str) -> list[PreferenceDraft]:
    """List drafts visible to their owner, including readiness for every slot."""

    draft_rows = select_authenticated(
        "preference_drafts", access_token, order="updated_at"
    )
    slot_rows = select_authenticated(
        "preference_slots", access_token, order="position", desc=False
    )
    slots_by_draft: dict[str, list[PreferenceSlot]] = {}
    for row in slot_rows:
        slots_by_draft.setdefault(str(row.get("draft_id") or ""), []).append(
            _slot_from_row(row)
        )

    drafts: list[PreferenceDraft] = []
    for row in draft_rows:
        draft_id = str(row.get("draft_id") or "")
        drafts.append(
            PreferenceDraft(
                draft_id=draft_id,
                owner_id=str(row.get("owner_id") or ""),
                title=str(row.get("title") or "Trip draft"),
                trip=TripBasics.model_validate(row.get("trip_json")),
                slots=tuple(slots_by_draft.get(draft_id, [])),
                updated_at=str(row.get("updated_at") or ""),
            )
        )
    return drafts


def get_preference_draft(draft_id: str, access_token: str) -> PreferenceDraft:
    """Return one owned draft from the caller's RLS-filtered result set."""

    for draft in list_preference_drafts(access_token):
        if draft.draft_id == draft_id:
            return draft
    raise LookupError("Preference draft was not found")
