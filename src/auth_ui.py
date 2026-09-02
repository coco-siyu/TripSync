"""Streamlit account flow for private, account-backed TripSync plans."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import streamlit as st

from src.auth import (
    AccountError,
    AccountSession,
    account_session_from_mapping,
    delete_account,
    refresh_account_session,
    request_password_recovery,
    sign_in,
    sign_out,
    sign_up,
    update_password,
    verify_password_recovery_token,
)
from src.supabase_store import account_feature_is_configured
from src.invitations import claim_trip_invitation
from src.preference_invitations import claim_preference_invitation
from src.trips import claim_anonymous_trips


AUTH_SESSION_KEY = "account_session"
ACCOUNT_NOTICE_KEY = "account_notice"
TRIP_TRANSFER_NOTICE_KEY = "account_trip_transfer_notice"
TRIP_TRANSFER_ERROR_KEY = "account_trip_transfer_error"
AUTH_VIEW_KEY = "account_auth_view"
PASSWORD_RECOVERY_SESSION_KEY = "account_password_recovery_session"
PASSWORD_RECOVERY_ERROR_KEY = "account_password_recovery_error"
PASSWORD_RECOVERY_EMAIL_SENT_KEY = "account_password_recovery_email_sent"
PASSWORD_RECOVERY_ENABLED = False
TRIP_INVITATION_NOTICE_KEY = "trip_invitation_notice"
PREFERENCE_ASSIGNMENT_KEY = "preference_assignment"
PREFERENCE_INVITATION_NOTICE_KEY = "preference_invitation_notice"


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


def _query_param_value(key: str) -> str:
    value = st.query_params.get(key, "")
    if isinstance(value, list):
        value = value[-1] if value else ""
    return str(value or "").strip()


def _clear_recovery_query_params() -> None:
    for key in ("token_hash", "type"):
        if key in st.query_params:
            del st.query_params[key]


def _consume_password_recovery_callback() -> bool:
    """Verify one recovery callback and remove its token from the browser URL."""

    if _query_param_value("type").casefold() != "recovery":
        return False
    token_hash = _query_param_value("token_hash")
    if not token_hash:
        return False
    _clear_recovery_query_params()
    st.session_state.app_workspace = "Account"
    st.session_state[AUTH_VIEW_KEY] = "recovery"
    _clear_account_session()
    try:
        recovery_session = verify_password_recovery_token(token_hash)
    except AccountError as error:
        st.session_state.pop(PASSWORD_RECOVERY_SESSION_KEY, None)
        st.session_state[PASSWORD_RECOVERY_ERROR_KEY] = str(error)
    else:
        st.session_state[PASSWORD_RECOVERY_SESSION_KEY] = recovery_session.as_dict()
        st.session_state.pop(PASSWORD_RECOVERY_ERROR_KEY, None)
    return True


def _use_account_session(session: AccountSession) -> None:
    st.session_state[AUTH_SESSION_KEY] = session.as_dict()
    st.session_state.feedback_session_id = session.user_id


def _clear_account_session() -> None:
    st.session_state[AUTH_SESSION_KEY] = None
    st.session_state.feedback_session_id = st.session_state.anonymous_session_id
    st.session_state.saved_trip_id = None
    st.session_state.saved_trip_owner_id = None
    st.session_state.saved_trip_access_role = "owner"
    st.session_state.saved_itinerary_version_id = None
    st.session_state.pop("open_saved_itinerary", None)
    st.session_state.pop("saved_trip_confirmation", None)
    st.session_state.pop(TRIP_TRANSFER_NOTICE_KEY, None)
    st.session_state.pop(TRIP_TRANSFER_ERROR_KEY, None)
    st.session_state.pop(PREFERENCE_ASSIGNMENT_KEY, None)


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
    st.session_state.setdefault("saved_trip_access_role", "owner")
    st.session_state.setdefault(AUTH_VIEW_KEY, "sign_in")
    if _consume_password_recovery_callback():
        st.session_state.feedback_session_id = st.session_state.anonymous_session_id
        return
    if account_session_from_mapping(
        st.session_state.get(PASSWORD_RECOVERY_SESSION_KEY)
    ):
        st.session_state.feedback_session_id = st.session_state.anonymous_session_id
        return
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


def handle_trip_invitation() -> None:
    """Route or claim an invite after account state has been reconciled."""

    token = _query_param_value("invite")
    if not token:
        return
    session = current_account_session()
    if session is None:
        st.session_state.app_workspace = "Account"
        st.session_state[ACCOUNT_NOTICE_KEY] = (
            "Sign in or create an account to add this shared trip to My trips."
        )
        return

    del st.query_params["invite"]
    st.session_state.app_workspace = "My trips"
    try:
        identity = claim_trip_invitation(token, session.access_token)
    except Exception:
        st.session_state[TRIP_INVITATION_NOTICE_KEY] = {
            "level": "error",
            "message": (
                "This trip invitation is invalid, expired, or has been revoked."
            ),
        }
    else:
        collaborator = identity.access_role == "collaborator"
        st.session_state[TRIP_INVITATION_NOTICE_KEY] = {
            "level": "success",
            "message": (
                "The shared trip is ready. You can create itinerary versions "
                "without changing the owner's trip brief."
                if collaborator
                else "The shared trip is now available here in read-only mode."
            ),
            "record_key": f"{identity.owner_id}:{identity.trip_id}",
        }


def handle_preference_invitation() -> None:
    """Route or claim one named traveler-profile invitation."""

    token = _query_param_value("profile_invite")
    if not token:
        return
    session = current_account_session()
    if session is None:
        st.session_state.app_workspace = "Account"
        st.session_state[ACCOUNT_NOTICE_KEY] = (
            "Sign in or create an account to add your preferences to this trip."
        )
        return

    del st.query_params["profile_invite"]
    try:
        assignment = claim_preference_invitation(token, session.access_token)
    except Exception:
        st.session_state.app_workspace = "Account"
        st.session_state[PREFERENCE_INVITATION_NOTICE_KEY] = {
            "level": "error",
            "message": (
                "This preference invitation is invalid, expired, or already "
                "belongs to another account."
            ),
        }
    else:
        st.session_state[PREFERENCE_ASSIGNMENT_KEY] = {
            "draft_id": assignment.draft_id,
            "slot_id": assignment.slot_id,
            "traveler_name": assignment.traveler_name,
            "trip": assignment.trip.model_dump(mode="json"),
            "profile": (
                assignment.profile.model_dump(mode="json")
                if assignment.profile is not None
                else None
            ),
        }
        st.session_state.trip_basics = assignment.trip.model_dump(mode="json")
        st.session_state.planner_step = "profile"
        st.session_state.app_workspace = "Plan a trip"


def _render_password_security(session: AccountSession) -> None:
    st.caption("Change the password used to sign in to this account.")
    with st.form("change-password-form", border=True, clear_on_submit=True):
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


def _render_privacy_controls(session: AccountSession) -> None:
    st.caption(
        "Deleting your account permanently removes its saved trips, every itinerary "
        "version, group preference drafts or submitted profiles, and feedback "
        "connected to the account. The shared activity catalog is not personal "
        "account data and is not affected."
    )
    st.warning(
        "This cannot be undone. Your current browser plan will also be cleared.",
        icon=":material/warning:",
    )
    with st.form("delete-account-form", border=True, clear_on_submit=True):
        current_password = st.text_input(
            "Current password",
            type="password",
            autocomplete="current-password",
            key="delete-account-password",
        )
        confirmation_email = st.text_input(
            "Type your account email to confirm",
            placeholder=session.email,
            key="delete-account-confirmation-email",
        )
        delete_requested = st.form_submit_button(
            "Permanently delete account",
            icon=":material/delete_forever:",
        )
    if not delete_requested:
        return
    if confirmation_email.strip().casefold() != session.email.casefold():
        st.error(
            "Enter the signed-in account email exactly to confirm deletion.",
            icon=":material/error:",
        )
        return
    try:
        delete_account(session, current_password)
    except AccountError as error:
        st.error(str(error), icon=":material/error:")
        return

    _clear_account_session()
    st.session_state.anonymous_session_id = uuid4().hex
    st.session_state.feedback_session_id = st.session_state.anonymous_session_id
    st.session_state[AUTH_VIEW_KEY] = "sign_in"
    st.session_state.pop(PASSWORD_RECOVERY_SESSION_KEY, None)
    st.session_state.pop(PASSWORD_RECOVERY_ERROR_KEY, None)
    from src.ui import _start_new_trip

    _start_new_trip()
    st.session_state["app_notice"] = (
        "Your account and its saved TripSync data were permanently deleted. "
        "You are now planning anonymously."
    )
    st.rerun()


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

    security_tab, privacy_tab = st.tabs(
        ["Security", "Privacy & deletion"],
        key="account-settings-tab",
        on_change="rerun",
    )
    if security_tab.open:
        with security_tab:
            _render_password_security(session)
    if privacy_tab.open:
        with privacy_tab:
            _render_privacy_controls(session)

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
        confirmed_password = None
        if mode == "Create account":
            confirmed_password = st.text_input(
                "Confirm password",
                type="password",
                autocomplete="new-password",
            )
            st.caption("Use at least 8 characters.")
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
    if PASSWORD_RECOVERY_ENABLED and mode == "Sign in" and st.button(
        "Forgot password?",
        type="tertiary",
        icon=":material/help:",
        key="open-password-recovery",
    ):
        st.session_state[AUTH_VIEW_KEY] = "request_recovery"
        st.session_state.pop(PASSWORD_RECOVERY_EMAIL_SENT_KEY, None)
        st.rerun()
    if not submitted:
        return
    if mode == "Create account" and password != confirmed_password:
        st.error("The passwords do not match.", icon=":material/error:")
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


def _render_password_recovery_request() -> None:
    st.subheader("Reset your password")
    st.caption(
        "Enter your account email. For privacy, TripSync gives the same response "
        "whether or not an account exists."
    )
    with st.form("password-recovery-request-form", border=True):
        email = st.text_input(
            "Email",
            autocomplete="email",
            placeholder="you@example.com",
            key="password-recovery-email",
        )
        submitted = st.form_submit_button(
            "Send reset email",
            type="primary",
            icon=":material/outgoing_mail:",
            width="stretch",
        )
    if submitted:
        try:
            request_password_recovery(
                email,
                redirect_to=current_app_origin(),
            )
        except AccountError as error:
            st.error(str(error), icon=":material/error:")
        else:
            st.session_state[PASSWORD_RECOVERY_EMAIL_SENT_KEY] = True
    if st.session_state.get(PASSWORD_RECOVERY_EMAIL_SENT_KEY):
        st.success(
            "If an account exists for that email, a reset message is on its way.",
            icon=":material/mark_email_read:",
        )
    if st.button(
        "Back to sign in",
        type="tertiary",
        icon=":material/arrow_back:",
        key="cancel-password-recovery-request",
    ):
        st.session_state[AUTH_VIEW_KEY] = "sign_in"
        st.session_state.pop(PASSWORD_RECOVERY_EMAIL_SENT_KEY, None)
        st.rerun()


def _render_recovered_password_form(recovery_session: AccountSession) -> None:
    st.subheader("Choose a new password")
    st.caption(
        f"The reset link for {recovery_session.email} was verified. Choose a new "
        "password to finish."
    )
    with st.form("recovered-password-form", border=True, clear_on_submit=True):
        new_password = st.text_input(
            "New password",
            type="password",
            autocomplete="new-password",
            key="recovered-new-password",
        )
        confirmed_password = st.text_input(
            "Confirm new password",
            type="password",
            autocomplete="new-password",
            key="recovered-confirmed-password",
        )
        submitted = st.form_submit_button(
            "Reset password",
            type="primary",
            icon=":material/password:",
            width="stretch",
        )
    if submitted:
        if new_password != confirmed_password:
            st.error("The new passwords do not match.", icon=":material/error:")
        else:
            try:
                refreshed_session = update_password(recovery_session, new_password)
            except AccountError as error:
                st.error(str(error), icon=":material/error:")
            else:
                st.session_state.pop(PASSWORD_RECOVERY_SESSION_KEY, None)
                st.session_state.pop(PASSWORD_RECOVERY_ERROR_KEY, None)
                st.session_state[AUTH_VIEW_KEY] = "sign_in"
                _finish_sign_in(refreshed_session)
                st.session_state[ACCOUNT_NOTICE_KEY] = (
                    "Your password was reset and you are now signed in."
                )
                st.rerun()
    if st.button(
        "Cancel password reset",
        type="tertiary",
        icon=":material/close:",
        key="cancel-recovered-password",
    ):
        st.session_state.pop(PASSWORD_RECOVERY_SESSION_KEY, None)
        st.session_state.pop(PASSWORD_RECOVERY_ERROR_KEY, None)
        st.session_state[AUTH_VIEW_KEY] = "sign_in"
        st.rerun()


def render_account_workspace() -> None:
    """Render account access without blocking the anonymous planning demo."""

    st.markdown('<div class="ts-section-label">Your account</div>', unsafe_allow_html=True)
    st.title("Keep your trips with you")
    if notice := st.session_state.pop(ACCOUNT_NOTICE_KEY, None):
        st.info(notice, icon=":material/info:")
    preference_notice = st.session_state.pop(
        PREFERENCE_INVITATION_NOTICE_KEY, None
    )
    if isinstance(preference_notice, dict):
        st.error(
            str(
                preference_notice.get("message")
                or "The invitation could not be opened."
            ),
            icon=":material/link_off:",
        )

    recovery_session = account_session_from_mapping(
        st.session_state.get(PASSWORD_RECOVERY_SESSION_KEY)
    )
    if recovery_session is not None:
        _render_recovered_password_form(recovery_session)
        return
    if error := st.session_state.pop(PASSWORD_RECOVERY_ERROR_KEY, None):
        st.error(error, icon=":material/link_off:")
        st.session_state[AUTH_VIEW_KEY] = "request_recovery"

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
    if (
        PASSWORD_RECOVERY_ENABLED
        and st.session_state.get(AUTH_VIEW_KEY) == "request_recovery"
    ):
        _render_password_recovery_request()
    else:
        if st.session_state.get(AUTH_VIEW_KEY) == "request_recovery":
            st.session_state[AUTH_VIEW_KEY] = "sign_in"
        _render_auth_forms()
