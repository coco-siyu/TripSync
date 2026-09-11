"""Shared Streamlit presentation for named traveler response status."""

from __future__ import annotations

from html import escape

import streamlit as st

from src.auth_ui import current_account_session, current_app_origin
from src.models import TripRequest
from src.preference_invitations import (
    PreferenceDraft,
    build_preference_invitation_url,
    create_slot_invitation,
)


def render_preference_draft_status(
    draft: PreferenceDraft,
    *,
    key_prefix: str = "preference-status",
) -> TripRequest | None:
    """Render current named responses and return the buildable trip, if any."""

    account = current_account_session()
    if account is None:
        st.warning(
            "Sign back in to check this group’s responses.",
            icon=":material/login:",
        )
        return None
    origin = current_app_origin()
    links = st.session_state.preference_invite_links
    if not isinstance(links, dict):
        links = {}
        st.session_state.preference_invite_links = links

    complete_count = sum(slot.is_complete for slot in draft.slots)
    st.subheader(f"{complete_count} of {len(draft.slots)} profiles ready")
    st.progress(
        int(complete_count / len(draft.slots) * 100),
        text=(
            "Everyone has replied."
            if draft.is_ready
            else (
                "You can build now; remaining travelers can reply later."
                if draft.can_build
                else "At least two profiles are needed to build the first draft."
            )
        ),
    )
    for slot in draft.slots:
        with st.container(
            border=True,
            key=f"{key_prefix}-slot-{slot.slot_id}",
        ):
            status_col, action_col = st.columns(
                [3, 2], vertical_alignment="center"
            )
            status_col.markdown(f"**{escape(slot.traveler_name)}**")
            if slot.is_complete:
                action_col.success("Ready", icon=":material/check_circle:")
            elif slot.member_id:
                action_col.info("Joined · waiting", icon=":material/edit_note:")
            else:
                action_col.warning("Not joined", icon=":material/schedule:")

            link_details = links.get(slot.slot_id)
            if (
                not slot.is_complete
                and isinstance(link_details, dict)
                and link_details.get("url")
            ):
                st.code(str(link_details["url"]), language=None)
                st.caption(
                    "Send only to this traveler. The link expires in 7 days."
                )
            elif not slot.is_complete and slot.position > 0 and not slot.member_id:
                if st.button(
                    f"Create a new link for {slot.traveler_name}",
                    key=f"{key_prefix}-refresh-link-{slot.slot_id}",
                    icon=":material/link:",
                    disabled=origin is None,
                ):
                    try:
                        invitation = create_slot_invitation(
                            draft.draft_id,
                            slot,
                            account.user_id,
                            account.access_token,
                        )
                        assert origin is not None
                        links[slot.slot_id] = {
                            "traveler_name": invitation.traveler_name,
                            "url": build_preference_invitation_url(
                                origin, invitation.token
                            ),
                            "expires_at": invitation.expires_at,
                        }
                    except Exception:
                        st.error(
                            "TripSync could not create a fresh link.",
                            icon=":material/error:",
                        )
                    else:
                        st.rerun()

    st.button(
        "Refresh responses",
        icon=":material/refresh:",
        key=f"{key_prefix}-refresh-{draft.draft_id}",
    )
    return draft.to_trip_request() if draft.can_build else None
