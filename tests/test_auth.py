"""Tests for account sessions and dormant Phase 2 invitation helpers."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.auth import (
    AccountError,
    AccountSession,
    account_session_from_mapping,
    delete_account,
    refresh_account_session,
    request_password_recovery,
    sign_in,
    sign_up,
    update_password,
    verify_password_recovery_token,
)
from src.auth_ui import app_origin_from_url
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

    def test_sign_up_sends_the_validated_current_app_origin(self) -> None:
        sign_up_mock = Mock(return_value=_auth_response(session=False))
        auth_client = SimpleNamespace(auth=SimpleNamespace(sign_up=sign_up_mock))
        with (
            patch("src.auth.auth_is_configured", return_value=True),
            patch("src.auth.public_client", return_value=auth_client),
        ):
            result = sign_up(
                " Traveler@Example.com ",
                "password123",
                email_redirect_to="http://localhost:8501/account?step=signup#ignored",
            )

        self.assertTrue(result.confirmation_required)
        sign_up_mock.assert_called_once_with(
            {
                "email": "traveler@example.com",
                "password": "password123",
                "options": {
                    "email_redirect_to": "http://localhost:8501",
                },
            }
        )

    def test_rejects_an_unsafe_confirmation_redirect(self) -> None:
        with (
            patch("src.auth.auth_is_configured", return_value=True),
            self.assertRaisesRegex(AccountError, "confirmation address"),
        ):
            sign_up(
                "traveler@example.com",
                "password123",
                email_redirect_to="javascript:alert(1)",
            )

    def test_streamlit_url_resolves_to_local_or_deployed_origin(self) -> None:
        self.assertEqual(
            app_origin_from_url("http://localhost:8501/?view=account"),
            "http://localhost:8501",
        )
        self.assertEqual(
            app_origin_from_url("https://tripsync.streamlit.app/Account"),
            "https://tripsync.streamlit.app",
        )
        self.assertIsNone(app_origin_from_url("javascript:alert(1)"))

    def test_update_password_reauthenticates_before_updating_user(self) -> None:
        set_session_mock = Mock(return_value=_auth_response())
        update_user_mock = Mock()
        auth_client = SimpleNamespace(
            auth=SimpleNamespace(
                set_session=set_session_mock,
                update_user=update_user_mock,
            )
        )
        session = AccountSession(
            "user-123",
            "traveler@example.com",
            "access-token",
            "refresh-token",
            4_000_000_000,
        )
        with (
            patch("src.auth.auth_is_configured", return_value=True),
            patch("src.auth.public_client", return_value=auth_client),
        ):
            refreshed = update_password(session, "new-password123")

        set_session_mock.assert_called_once_with("access-token", "refresh-token")
        update_user_mock.assert_called_once_with({"password": "new-password123"})
        self.assertEqual(refreshed.user_id, "user-123")

    def test_password_recovery_request_uses_normalized_email_and_origin(self) -> None:
        reset_mock = Mock()
        auth_client = SimpleNamespace(
            auth=SimpleNamespace(reset_password_for_email=reset_mock)
        )
        with (
            patch("src.auth.auth_is_configured", return_value=True),
            patch("src.auth.public_client", return_value=auth_client),
        ):
            request_password_recovery(
                " Traveler@Example.com ",
                redirect_to="http://localhost:8501/Account?ignored=yes",
            )

        reset_mock.assert_called_once_with(
            "traveler@example.com",
            {"redirect_to": "http://localhost:8501"},
        )

    def test_password_recovery_token_is_verified_as_recovery(self) -> None:
        verify_mock = Mock(return_value=_auth_response())
        auth_client = SimpleNamespace(auth=SimpleNamespace(verify_otp=verify_mock))
        with (
            patch("src.auth.auth_is_configured", return_value=True),
            patch("src.auth.public_client", return_value=auth_client),
        ):
            session = verify_password_recovery_token(" token-hash ")

        verify_mock.assert_called_once_with(
            {"token_hash": "token-hash", "type": "recovery"}
        )
        self.assertEqual(session.user_id, "user-123")

    def test_blank_password_recovery_token_is_rejected_before_network_call(
        self,
    ) -> None:
        with self.assertRaisesRegex(AccountError, "not valid"):
            verify_password_recovery_token("  ")

    def test_update_password_validates_length_before_network_call(self) -> None:
        session = AccountSession(
            "user-123",
            "traveler@example.com",
            "access-token",
            "refresh-token",
            4_000_000_000,
        )
        with self.assertRaisesRegex(AccountError, "at least 8"):
            update_password(session, "short")

    def test_delete_account_reauthenticates_and_targets_only_verified_user(
        self,
    ) -> None:
        session = AccountSession(
            "user-123",
            "traveler@example.com",
            "old-access-token",
            "old-refresh-token",
            4_000_000_000,
        )
        verified_session = AccountSession(
            "user-123",
            "traveler@example.com",
            "verified-access-token",
            "verified-refresh-token",
            4_100_000_000,
        )
        delete_user_mock = Mock()
        server_client = SimpleNamespace(
            auth=SimpleNamespace(
                admin=SimpleNamespace(delete_user=delete_user_mock)
            )
        )
        with (
            patch("src.auth.account_feature_is_configured", return_value=True),
            patch("src.auth.sign_in", return_value=verified_session) as sign_in_mock,
            patch("src.auth.rpc_authenticated") as rpc_mock,
            patch("src.auth.client", return_value=server_client),
        ):
            delete_account(session, "password123")

        sign_in_mock.assert_called_once_with(
            "traveler@example.com",
            "password123",
        )
        rpc_mock.assert_called_once_with(
            "delete_my_account_data",
            {},
            "verified-access-token",
        )
        delete_user_mock.assert_called_once_with(
            "user-123",
            should_soft_delete=False,
        )

    def test_delete_account_rejects_a_changed_identity(self) -> None:
        session = AccountSession(
            "user-123",
            "traveler@example.com",
            "old-access-token",
            "old-refresh-token",
            4_000_000_000,
        )
        other_session = AccountSession(
            "other-user",
            "traveler@example.com",
            "other-access-token",
            "other-refresh-token",
            4_100_000_000,
        )
        with (
            patch("src.auth.account_feature_is_configured", return_value=True),
            patch("src.auth.sign_in", return_value=other_session),
            patch("src.auth.rpc_authenticated") as rpc_mock,
            patch("src.auth.client") as client_mock,
            self.assertRaisesRegex(AccountError, "Sign in again"),
        ):
            delete_account(session, "password123")

        rpc_mock.assert_not_called()
        client_mock.assert_not_called()

    def test_delete_account_keeps_auth_user_when_data_cleanup_fails(self) -> None:
        session = AccountSession(
            "user-123",
            "traveler@example.com",
            "access-token",
            "refresh-token",
            4_000_000_000,
        )
        with (
            patch("src.auth.account_feature_is_configured", return_value=True),
            patch("src.auth.sign_in", return_value=session),
            patch(
                "src.auth.rpc_authenticated",
                side_effect=RuntimeError("database unavailable"),
            ),
            patch("src.auth.client") as client_mock,
            self.assertRaisesRegex(AccountError, "account remains active"),
        ):
            delete_account(session, "password123")

        client_mock.assert_not_called()

    def test_delete_account_explains_when_schema_function_is_missing(self) -> None:
        session = AccountSession(
            "user-123",
            "traveler@example.com",
            "access-token",
            "refresh-token",
            4_000_000_000,
        )
        missing_function = RuntimeError(
            "Could not find the function public.delete_my_account_data "
            "in the schema cache"
        )
        with (
            patch("src.auth.account_feature_is_configured", return_value=True),
            patch("src.auth.sign_in", return_value=session),
            patch("src.auth.rpc_authenticated", side_effect=missing_function),
            self.assertRaisesRegex(AccountError, "Reapply the current"),
        ):
            delete_account(session, "password123")

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
