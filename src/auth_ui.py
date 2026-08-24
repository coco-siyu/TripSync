"""Streamlit account flow for private, account-backed TripSync plans."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import streamlit as st

from src.auth import (
    AccountError,
    AccountSession,
    account_session_from_mapping,
    refresh_account_session,
    sign_in,
    sign_out,
    sign_up,
    update_password,
)
from src.supabase_store import account_feature_is_configured
from src.trips import claim_anonymous_trips


AUTH_SESSION_KEY = "account_session"
ACCOUNT_NOTICE_KEY = "account_notice"
TRIP_TRANSFER_NOTICE_KEY = "account_trip_transfer_notice"
TRIP_TRANSFER_ERROR_KEY = "account_trip_transfer_error"


def app_origin_from_url(value: str) -> str | None:
    """Reduce a Streamlit session URL to a safe confirmation origin."""

    parsed = urlsplit(value.strip())
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return urlunsplit((parsed.scheme.casefold(), parsed.netloc, "", "", ""))


def current_app_origin() -> str | None:
    """Return the active local or deployed TripSync origin."""

    return app_origin_from_url(str(st.context.url or ""))


def current_account_session() -> AccountSession | None:
    """Return the validated account for this browser tab, if any."""

    return account_session_from_mapping(st.session_state.get(AUTH_SESSION_KEY))


def _use_account_session(session: AccountSession) -> None:
    st.session_state[AUTH_SESSION_KEY] = session.as_dict()
    st.session_state.feedback_session_id = session.user_id


def _clear_account_session() -> None:
    st.session_state[AUTH_SESSION_KEY] = None
    st.session_state.feedback_session_id = st.session_state.anonymous_session_id
    st.session_state.saved_trip_id = None
    st.session_state.saved_trip_owner_id = None
    st.session_state.saved_itinerary_version_id = None
    st.session_state.pop("open_saved_itinerary", None)
    st.session_state.pop("saved_trip_confirmation", None)
    st.session_state.pop(TRIP_TRANSFER_NOTICE_KEY, None)
    st.session_state.pop(TRIP_TRANSFER_ERROR_KEY, None)


def _claim_browser_trips(session: AccountSession) -> None:
    """Atomically move this session's anonymous trips into an account."""

    anonymous_session_id = str(
        st.session_state.get("anonymous_session_id") or ""
    ).strip()
    if not anonymous_session_id or anonymous_session_id == session.user_id:
        return
    try:
        claimed_count = claim_anonymous_trips(
            anonymous_session_id,
            session.access_token,
        )
    except Exception:
        st.session_state[TRIP_TRANSFER_ERROR_KEY] = (
            "You are signed in, but TripSync could not move plans saved in this "
            "session into your account. You can retry from this page."
        )
        return
    st.session_state.pop(TRIP_TRANSFER_ERROR_KEY, None)
    if claimed_count:
        if st.session_state.get("saved_trip_owner_id") == anonymous_session_id:
            st.session_state.saved_trip_owner_id = session.user_id
        save_confirmation = st.session_state.get("saved_trip_confirmation")
        if isinstance(save_confirmation, dict):
            st.session_state.saved_trip_confirmation = {
                **save_confirmation,
                "account_backed": True,
            }
        noun = "plan" if claimed_count == 1 else "plans"
        st.session_state[TRIP_TRANSFER_NOTICE_KEY] = (
            f"Moved {claimed_count} {noun} saved in this session into your account."
        )


def _finish_sign_in(session: AccountSession) -> None:
    """Transfer anonymous plans before changing the active storage identity."""

    _claim_browser_trips(session)
    _use_account_session(session)


def initialize_account_state() -> None:
    """Initialize, refresh, and reconcile account state once per app rerun."""

    st.session_state.setdefault(
        "anonymous_session_id",
        st.session_state.get("feedback_session_id") or uuid4().hex,
    )
    st.session_state.setdefault(AUTH_SESSION_KEY, None)
    st.session_state.setdefault("saved_trip_owner_id", None)
    session = current_account_session()
    if session is None:
        st.session_state.feedback_session_id = st.session_state.anonymous_session_id
        return
    try:
        refreshed = refresh_account_session(session)
    except AccountError as error:
        _clear_account_session()
        st.session_state[ACCOUNT_NOTICE_KEY] = str(error)
        st.session_state.app_workspace = "Account"
        return
    _use_account_session(refreshed)


def _render_signed_in_account(session: AccountSession) -> None:
    st.success(f"Signed in as {session.email}", icon=":material/verified_user:")
    st.caption(
        "Saved trips are private to this account and are available after you sign "
        "in on another device."
    )
    if notice := st.session_state.get(TRIP_TRANSFER_NOTICE_KEY):
        st.success(notice, icon=":material/cloud_done:")
        if st.button(
            "Got it",
            icon=":material/check:",
            key="dismiss-transfer-notice-account",
        ):
            st.session_state.pop(TRIP_TRANSFER_NOTICE_KEY, None)
            st.rerun()
    if error := st.session_state.get(TRIP_TRANSFER_ERROR_KEY):
        st.warning(error, icon=":material/sync_problem:")
        if st.button(
            "Retry moving session plans",
            icon=":material/sync:",
            key="retry-browser-trip-transfer",
        ):
            _claim_browser_trips(session)
            st.rerun()
    with st.expander(
        "Security",
        icon=":material/lock:",
    ):
        st.caption("Change the password used to sign in to this account.")
        with st.form("change-password-form", border=False, clear_on_submit=True):
            new_password = st.text_input(
                "New password",
                type="password",
                autocomplete="new-password",
            )
            confirmed_password = st.text_input(
                "Confirm new password",
                type="password",
                autocomplete="new-password",
            )
            change_password = st.form_submit_button(
                "Update password",
                icon=":material/password:",
            )
        if change_password:
            if new_password != confirmed_password:
                st.error("The new passwords do not match.", icon=":material/error:")
            else:
                try:
                    refreshed_session = update_password(session, new_password)
                except AccountError as error:
                    st.error(str(error), icon=":material/error:")
                else:
                    _use_account_session(refreshed_session)
                    st.success(
                        "Your password has been updated.",
                        icon=":material/check_circle:",
                    )
    if st.button("Sign out", icon=":material/logout:"):
        sign_out_error = False
        try:
            sign_out(session)
        except AccountError:
            sign_out_error = True
        _clear_account_session()
        if sign_out_error:
            st.session_state[ACCOUNT_NOTICE_KEY] = (
                "You are signed out on this device. TripSync could not revoke the "
                "remote session, so sign out other sessions from Supabase if needed."
            )
        st.rerun()


def _render_auth_forms() -> None:
    mode = st.segmented_control(
        "Account action",
        ["Sign in", "Create account"],
        default="Sign in",
        label_visibility="collapsed",
    )
    with st.form("account-form", border=True):
        email = st.text_input(
            "Email",
            autocomplete="email",
            placeholder="you@example.com",
        )
        password = st.text_input(
            "Password",
            type="password",
            autocomplete=(
                "current-password" if mode == "Sign in" else "new-password"
            ),
        )
        submitted = st.form_submit_button(
            mode or "Sign in",
            type="primary",
            icon=(
                ":material/login:"
                if mode == "Sign in"
                else ":material/person_add:"
            ),
            width="stretch",
        )
    if not submitted:
        return
    try:
        if mode == "Create account":
            result = sign_up(
                email,
                password,
                email_redirect_to=current_app_origin(),
            )
            if result.confirmation_required:
                st.success(
                    "Check your email to confirm the account, then return here to sign in.",
                    icon=":material/mark_email_read:",
                )
                return
            assert result.session is not None
            _finish_sign_in(result.session)
        else:
            _finish_sign_in(sign_in(email, password))
    except AccountError as error:
        st.error(str(error), icon=":material/error:")
        return
    st.rerun()


def render_account_workspace() -> None:
    """Render account access without blocking the anonymous planning demo."""

    st.markdown('<div class="ts-section-label">Your account</div>', unsafe_allow_html=True)
    st.title("Keep your trips with you")
    if notice := st.session_state.pop(ACCOUNT_NOTICE_KEY, None):
        st.info(notice, icon=":material/info:")

    session = current_account_session()
    if session is not None:
        _render_signed_in_account(session)
        return
    if not account_feature_is_configured():
        st.info(
            "Account access is not enabled yet. Apply the Phase 1 Supabase schema "
            "and add the URL, secret key, and publishable key to deployment secrets.",
            icon=":material/admin_panel_settings:",
        )
        return
    st.caption(
        "When you sign in, plans already saved in this active session move into your "
        "private account."
    )
    _render_auth_forms()
