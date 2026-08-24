"""Tests for account sessions and dormant Phase 2 invitation helpers."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from src.auth import (
    AccountError,
    AccountSession,
    account_session_from_mapping,
    refresh_account_session,
    sign_in,
    sign_up,
)
from src.invitations import (
    build_invitation_url,
    claim_trip_invitation,
    create_trip_invitation,
    invitation_token_hash,
    revoke_trip_invitations,
)


def _auth_response(*, session: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        user=SimpleNamespace(id="user-123", email="Traveler@Example.com"),
        session=(
            SimpleNamespace(
                access_token="access-token",
                refresh_token="refresh-token",
                expires_at=4_000_000_000,
            )
            if session
            else None
        ),
    )


class AccountTests(unittest.TestCase):
    def test_validates_serialized_account_session(self) -> None:
        session = account_session_from_mapping(
            {
                "user_id": "user-123",
                "email": "traveler@example.com",
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "expires_at": 4_000_000_000,
            }
        )

        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(session.user_id, "user-123")
        self.assertIsNone(account_session_from_mapping({"email": "incomplete"}))

    def test_sign_in_normalizes_email_and_returns_verified_session(self) -> None:
        client = SimpleNamespace(
            auth=SimpleNamespace(sign_in_with_password=lambda credentials: _auth_response())
        )
        with (
            patch("src.auth.auth_is_configured", return_value=True),
            patch("src.auth.public_client", return_value=client),
        ):
            session = sign_in(" Traveler@Example.com ", "password123")

        self.assertEqual(session.email, "Traveler@Example.com")
        self.assertEqual(session.user_id, "user-123")

    def test_sign_up_requires_a_reasonable_password_before_network_call(self) -> None:
        with self.assertRaisesRegex(AccountError, "at least 8"):
            sign_up("traveler@example.com", "short")

    def test_unexpired_session_does_not_refresh(self) -> None:
        session = AccountSession(
            "user-123",
            "traveler@example.com",
            "access-token",
            "refresh-token",
            4_000_000_000,
        )
        with patch("src.auth.public_client") as client_mock:
            refreshed = refresh_account_session(session)

        self.assertIs(refreshed, session)
        client_mock.assert_not_called()


class InvitationTests(unittest.TestCase):
    def test_builds_invitation_url_without_leaking_hash_details(self) -> None:
        url = build_invitation_url(
            "https://tripsync.example/app?source=group",
            "secret-capability",
        )

        self.assertEqual(
            url,
            "https://tripsync.example/app?source=group&invite=secret-capability",
        )
        self.assertEqual(
            invitation_token_hash("secret-capability"),
            "4f486bc01a193c0320c6a052493900485d005feed6ffc0733a6ed4e37172e91a",
        )

    def test_create_invitation_stores_only_a_hash(self) -> None:
        now = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
        with (
            patch("src.invitations.secrets.token_urlsafe", return_value="x" * 43),
            patch("src.invitations.insert_authenticated") as insert_mock,
        ):
            invitation = create_trip_invitation(
                "trip-123",
                "owner-123",
                "access-token",
                now=now,
            )

        row = insert_mock.call_args.args[1]
        self.assertNotIn(invitation.token, row.values())
        self.assertEqual(row["token_hash"], invitation_token_hash(invitation.token))
        self.assertEqual(row["trip_id"], "trip-123")

    def test_claim_and_revoke_use_authenticated_database_operations(self) -> None:
        with patch(
            "src.invitations.rpc_authenticated",
            return_value="trip-123",
        ) as rpc_mock:
            trip_id = claim_trip_invitation("x" * 43, "access-token")
        self.assertEqual(trip_id, "trip-123")
        rpc_mock.assert_called_once_with(
            "claim_trip_invitation",
            {"invite_token": "x" * 43},
            "access-token",
        )

        with patch("src.invitations.delete_authenticated") as delete_mock:
            revoke_trip_invitations("trip-123", "access-token")
        delete_mock.assert_called_once_with(
            "trip_invitations",
            "access-token",
            filters={"trip_id": "trip-123"},
        )


if __name__ == "__main__":
    unittest.main()
