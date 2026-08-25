"""Supabase email authentication without sharing sessions across app users."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import logging
from time import time
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv

from src.supabase_store import (
    account_feature_is_configured,
    auth_is_configured,
    client,
    public_client,
    rpc_authenticated,
)


load_dotenv()


LOGGER = logging.getLogger(__name__)


class AccountError(RuntimeError):
    """Raised when an account action cannot be completed safely."""


@dataclass(frozen=True)
class AccountSession:
    """The minimal Supabase session data kept in Streamlit session state."""

    user_id: str
    email: str
    access_token: str
    refresh_token: str
    expires_at: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SignUpResult:
    session: AccountSession | None
    confirmation_required: bool


def account_session_from_mapping(
    value: Mapping[str, Any] | None,
) -> AccountSession | None:
    """Validate session-state data before using its identity or tokens."""

    if not value:
        return None
    try:
        session = AccountSession(
            user_id=str(value["user_id"]).strip(),
            email=str(value["email"]).strip(),
            access_token=str(value["access_token"]).strip(),
            refresh_token=str(value["refresh_token"]).strip(),
            expires_at=int(value["expires_at"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if not all(
        (
            session.user_id,
            session.email,
            session.access_token,
            session.refresh_token,
        )
    ):
        return None
    return session


def _session_from_response(response: Any) -> AccountSession | None:
    remote_session = getattr(response, "session", None)
    user = getattr(response, "user", None)
    if remote_session is None or user is None:
        return None
    user_id = str(getattr(user, "id", "") or "").strip()
    email = str(getattr(user, "email", "") or "").strip()
    access_token = str(getattr(remote_session, "access_token", "") or "").strip()
    refresh_token = str(getattr(remote_session, "refresh_token", "") or "").strip()
    expires_at = int(getattr(remote_session, "expires_at", 0) or 0)
    if not all((user_id, email, access_token, refresh_token, expires_at)):
        raise AccountError("Supabase returned an incomplete account session.")
    return AccountSession(
        user_id=user_id,
        email=email,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
    )


def sign_in(email: str, password: str) -> AccountSession:
    """Authenticate one user with email and password."""

    normalized_email = email.strip().casefold()
    if not normalized_email or not password:
        raise AccountError("Enter both your email and password.")
    if not auth_is_configured():
        raise AccountError("Supabase Auth is not configured for this deployment.")
    try:
        response = public_client().auth.sign_in_with_password(
            {"email": normalized_email, "password": password}
        )
        session = _session_from_response(response)
    except Exception as error:
        raise AccountError("That email and password could not be verified.") from error
    if session is None:
        raise AccountError("Confirm your email before signing in.")
    return session


def _validated_redirect_origin(value: str) -> str:
    """Return one safe HTTP(S) origin for a Supabase confirmation email."""

    parsed = urlsplit(value.strip())
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise AccountError("The account confirmation address is not valid.")
    return urlunsplit((parsed.scheme.casefold(), parsed.netloc, "", "", ""))


def sign_up(
    email: str,
    password: str,
    *,
    email_redirect_to: str | None = None,
) -> SignUpResult:
    """Create an account, respecting the project's email-confirmation setting."""

    normalized_email = email.strip().casefold()
    if not normalized_email or not password:
        raise AccountError("Enter both your email and password.")
    if len(password) < 8:
        raise AccountError("Choose a password with at least 8 characters.")
    if not auth_is_configured():
        raise AccountError("Supabase Auth is not configured for this deployment.")
    credentials: dict[str, Any] = {
        "email": normalized_email,
        "password": password,
    }
    if email_redirect_to:
        credentials["options"] = {
            "email_redirect_to": _validated_redirect_origin(email_redirect_to),
        }
    try:
        response = public_client().auth.sign_up(credentials)
        session = _session_from_response(response)
    except AccountError:
        raise
    except Exception as error:
        raise AccountError("TripSync could not create that account.") from error
    return SignUpResult(
        session=session,
        confirmation_required=session is None,
    )


def request_password_recovery(
    email: str,
    *,
    redirect_to: str | None = None,
) -> None:
    """Ask Supabase to send a non-enumerating password recovery email."""

    normalized_email = email.strip().casefold()
    if not normalized_email:
        raise AccountError("Enter the email address for your account.")
    if not auth_is_configured():
        raise AccountError("Supabase Auth is not configured for this deployment.")
    options: dict[str, str] = {}
    if redirect_to:
        options["redirect_to"] = _validated_redirect_origin(redirect_to)
    try:
        public_client().auth.reset_password_for_email(
            normalized_email,
            options or None,
        )
    except AccountError:
        raise
    except Exception as error:
        raise AccountError(
            "TripSync could not send a reset email. Try again shortly."
        ) from error


def verify_password_recovery_token(token_hash: str) -> AccountSession:
    """Exchange one Supabase recovery token hash for a short-lived session."""

    normalized_token = token_hash.strip()
    if not normalized_token or len(normalized_token) > 1024:
        raise AccountError("That password reset link is not valid.")
    if not auth_is_configured():
        raise AccountError("Supabase Auth is not configured for this deployment.")
    try:
        response = public_client().auth.verify_otp(
            {
                "token_hash": normalized_token,
                "type": "recovery",
            }
        )
        session = _session_from_response(response)
    except Exception as error:
        raise AccountError(
            "That password reset link is invalid or expired. Request a new one."
        ) from error
    if session is None:
        raise AccountError(
            "That password reset link is invalid or expired. Request a new one."
        )
    return session


def update_password(
    session: AccountSession,
    new_password: str,
) -> AccountSession:
    """Update the password for one verified account session."""

    if len(new_password) < 8:
        raise AccountError("Choose a password with at least 8 characters.")
    if not auth_is_configured():
        raise AccountError("Supabase Auth is not configured for this deployment.")
    try:
        active_client = public_client()
        response = active_client.auth.set_session(
            session.access_token,
            session.refresh_token,
        )
        refreshed_session = _session_from_response(response)
        if refreshed_session is None:
            raise AccountError("Sign in again before changing your password.")
        active_client.auth.update_user({"password": new_password})
    except AccountError:
        raise
    except Exception as error:
        raise AccountError(
            "TripSync could not update your password. Sign in again and retry."
        ) from error
    return refreshed_session


def delete_account(session: AccountSession, current_password: str) -> None:
    """Permanently delete one freshly reauthenticated account and its data."""

    if not current_password:
        raise AccountError("Enter your current password to delete this account.")
    if not account_feature_is_configured():
        raise AccountError("Account deletion is not configured for this deployment.")

    verified_session = sign_in(session.email, current_password)
    if verified_session.user_id != session.user_id:
        raise AccountError("Sign in again before deleting this account.")

    try:
        rpc_authenticated(
            "delete_my_account_data",
            {},
            verified_session.access_token,
        )
    except Exception as error:
        LOGGER.exception("Supabase account-data cleanup failed")
        error_code = str(getattr(error, "code", "") or "").casefold()
        error_detail = str(error).casefold()
        if error_code == "pgrst202" or "could not find the function" in error_detail:
            message = (
                "Account deletion is not installed in Supabase yet. Reapply the "
                "current supabase/schema.sql file, then retry."
            )
        elif error_code == "42501" or "permission denied for function" in error_detail:
            message = (
                "Supabase has not granted account deletion to signed-in users. "
                "Reapply the current supabase/schema.sql file, then sign in again."
            )
        else:
            message = (
                "TripSync could not delete your account data. Your account remains "
                "active; retry shortly."
            )
        raise AccountError(
            message
        ) from error

    try:
        client().auth.admin.delete_user(
            verified_session.user_id,
            should_soft_delete=False,
        )
    except Exception as error:
        raise AccountError(
            "Your TripSync data was deleted, but the account itself could not be "
            "removed. Retry account deletion."
        ) from error


def refresh_account_session(
    session: AccountSession,
    *,
    refresh_window_seconds: int = 90,
) -> AccountSession:
    """Refresh a nearly expired token without a network call on every rerun."""

    if session.expires_at > int(time()) + refresh_window_seconds:
        return session
    try:
        response = public_client().auth.set_session(
            session.access_token,
            session.refresh_token,
        )
        refreshed = _session_from_response(response)
    except Exception as error:
        raise AccountError("Your session expired. Sign in again.") from error
    if refreshed is None:
        raise AccountError("Your session expired. Sign in again.")
    return refreshed


def sign_out(session: AccountSession) -> None:
    """Revoke the current refresh token before clearing local session state."""

    try:
        active_client = public_client()
        active_client.auth.set_session(session.access_token, session.refresh_token)
        active_client.auth.sign_out()
    except Exception as error:
        raise AccountError("TripSync could not sign out cleanly. Try again.") from error
