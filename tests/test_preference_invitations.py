"""Tests for named traveler preference invitations."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from tests import configure_test_output

configure_test_output()

from src.invitations import invitation_token_hash
from src.models import TravelerProfile, TripBasics
from src.preference_invitations import (
    PreferenceAssignment,
    build_preference_invitation_url,
    claim_preference_invitation,
    create_preference_draft,
    list_preference_drafts,
    submit_preference_profile,
)


def _trip() -> TripBasics:
    return TripBasics(
        destination="Rome",
        country="Italy",
        days=3,
        budget_level="moderate",
        pace="balanced",
    )


def _profile(name: str) -> TravelerProfile:
    return TravelerProfile(
        name=name,
        interests=["art"],
        walking_tolerance="moderate",
    )


class PreferenceInvitationTests(unittest.TestCase):
    def test_builds_a_separate_preference_invitation_url(self) -> None:
        url = build_preference_invitation_url(
            "https://tripsync.example/app?source=group",
            "secret-capability-that-is-long-enough-123",
        )

        self.assertEqual(
            url,
            "https://tripsync.example/app?source=group&profile_invite="
            "secret-capability-that-is-long-enough-123",
        )

    def test_create_draft_sends_only_hashes_and_named_slots(self) -> None:
        now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
        ids = iter(
            [
                SimpleNamespace(hex="d" * 32),
                SimpleNamespace(hex="a" * 32),
                SimpleNamespace(hex="b" * 32),
            ]
        )
        with (
            patch("src.preference_invitations.uuid4", side_effect=ids),
            patch(
                "src.preference_invitations.secrets.token_urlsafe",
                return_value="x" * 43,
            ),
            patch("src.preference_invitations.rpc_authenticated") as rpc_mock,
        ):
            creation = create_preference_draft(
                _trip(), _profile("Coco"), ["Sam"], "owner-user", "access-token",
                now=now,
            )

        params = rpc_mock.call_args.args[1]
        self.assertEqual(params["target_draft_id"], "d" * 32)
        self.assertEqual(
            [slot["traveler_name"] for slot in params["slot_payloads"]],
            ["Coco", "Sam"],
        )
        self.assertNotIn("x" * 43, repr(params))
        self.assertEqual(
            params["slot_payloads"][1]["token_hash"],
            invitation_token_hash("x" * 43),
        )
        self.assertEqual(creation.invitations[0].traveler_name, "Sam")

    def test_create_draft_rejects_duplicate_named_travelers(self) -> None:
        with (
            patch("src.preference_invitations.rpc_authenticated") as rpc_mock,
            self.assertRaisesRegex(ValueError, "unique"),
        ):
            create_preference_draft(
                _trip(), _profile("Coco"), [" coco "], "owner-user", "token"
            )
        rpc_mock.assert_not_called()

    def test_claim_and_submit_use_guarded_rpcs(self) -> None:
        response = {
            "draft_id": "d" * 32,
            "slot_id": "s" * 32,
            "traveler_name": "Sam",
            "trip": _trip().model_dump(mode="json"),
            "profile": None,
        }
        with patch(
            "src.preference_invitations.rpc_authenticated", return_value=response
        ) as claim_mock:
            assignment = claim_preference_invitation("x" * 43, "access-token")
        claim_mock.assert_called_once_with(
            "claim_preference_invitation",
            {"invite_token": "x" * 43},
            "access-token",
        )

        with patch("src.preference_invitations.rpc_authenticated") as submit_mock:
            submitted = submit_preference_profile(
                assignment, _profile("Sam"), "access-token"
            )
        self.assertEqual(submitted.name, "Sam")
        submit_mock.assert_called_once_with(
            "submit_preference_profile",
            {
                "target_draft_id": "d" * 32,
                "target_slot_id": "s" * 32,
                "profile_payload": _profile("Sam").model_dump(mode="json"),
            },
            "access-token",
        )

    def test_invitee_cannot_change_the_name_on_their_slot(self) -> None:
        assignment = PreferenceAssignment(
            "d" * 32, "s" * 32, "Sam", _trip()
        )
        with (
            patch("src.preference_invitations.rpc_authenticated") as rpc_mock,
            self.assertRaisesRegex(ValueError, "cannot be changed"),
        ):
            submit_preference_profile(
                assignment, _profile("Someone else"), "access-token"
            )
        rpc_mock.assert_not_called()

    def test_list_drafts_combines_rls_visible_slots_and_reports_readiness(self) -> None:
        draft_rows = [
            {
                "draft_id": "d" * 32,
                "owner_id": "owner-user",
                "title": "Rome · 3 days",
                "trip_json": _trip().model_dump(mode="json"),
                "updated_at": "2026-09-01T12:00:00+00:00",
            }
        ]
        slot_rows = [
            {
                "draft_id": "d" * 32,
                "slot_id": "a" * 32,
                "traveler_name": "Coco",
                "position": 0,
                "member_id": "owner-user",
                "profile_json": _profile("Coco").model_dump(mode="json"),
            },
            {
                "draft_id": "d" * 32,
                "slot_id": "b" * 32,
                "traveler_name": "Sam",
                "position": 1,
                "member_id": "member-user",
                "profile_json": _profile("Sam").model_dump(mode="json"),
            },
        ]
        with patch(
            "src.preference_invitations.select_authenticated",
            side_effect=[draft_rows, slot_rows],
        ):
            drafts = list_preference_drafts("access-token")

        self.assertEqual(len(drafts), 1)
        self.assertTrue(drafts[0].is_ready)
        self.assertEqual(
            [traveler.name for traveler in drafts[0].to_trip_request().travelers],
            ["Coco", "Sam"],
        )


if __name__ == "__main__":
    unittest.main()
