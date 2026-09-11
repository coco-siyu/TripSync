"""Saved-trip browser for the Streamlit workspace."""

from __future__ import annotations

from typing import Literal

import streamlit as st

from src.budget import daily_budget_label, euro_range
from src.auth_ui import (
    PREFERENCE_ASSIGNMENT_KEY,
    TRIP_INVITATION_NOTICE_KEY,
    TRIP_TRANSFER_NOTICE_KEY,
    current_account_session,
    current_app_origin,
)
from src.invitations import (
    SharedTripIdentity,
    build_invitation_url,
    create_trip_invitation,
    leave_shared_trip,
    revoke_trip_sharing,
)
from src.models import ItineraryPlan
from src.planner import itinerary_time_block_label
from src.preference_invitations import (
    PreferenceAssignment,
    PreferenceDraft,
    link_saved_trip_to_preference_draft,
    list_my_preference_assignments,
    list_preference_drafts,
)
from src.preference_status_ui import render_preference_draft_status
from src.trips import (
    SavedTrip,
    WORKING_DRAFT_ID,
    itinerary_versions,
    list_saved_trips,
    revise_itinerary_plan,
    save_trip,
    state_for_itinerary_version,
    state_for_working_itinerary_draft,
    working_itinerary_draft,
)


_RESTORED_STATE_DEFAULTS = {
    "selected_activity_ids": [],
    "dismissed_must_do_ids": [],
    "auto_select_must_dos": True,
    "itinerary_plan": None,
    "rejected_activities": {},
    "itinerary_narrative": None,
}
_OPEN_SAVED_ITINERARY_KEY = "open_saved_itinerary"
_SAVED_ITINERARY_FLASH_KEY = "saved_itinerary_flash"
_SAVED_TRIP_CONFIRMATION_KEY = "saved_trip_confirmation"
_SHARE_LINKS_KEY = "trip_share_links"
_OPEN_RESPONSE_STATUS_KEY = "open_preference_response_status"
_REVIEW_LATEST_RESPONSES_KEY = "review_latest_preference_responses"


def _resume_preference_draft(
    draft: PreferenceDraft,
    record: SavedTrip | None = None,
) -> None:
    """Open current responses while preserving the intended saved-trip identity."""

    st.session_state.trip_basics = draft.trip.model_dump(mode="json")
    st.session_state.trip_request = (
        draft.to_trip_request().model_dump(mode="json")
        if draft.can_build
        else None
    )
    st.session_state.traveler_count = len(draft.slots)
    st.session_state.preference_collection_mode = "Invite separately"
    st.session_state.active_preference_draft_id = draft.draft_id
    st.session_state.preference_invite_links = {}
    st.session_state.saved_trip_id = record.trip_id if record is not None else None
    st.session_state.saved_trip_owner_id = (
        record.owner_id if record is not None else None
    )
    st.session_state.saved_trip_access_role = (
        record.access_role if record is not None else "owner"
    )
    st.session_state.saved_itinerary_version_id = None
    st.session_state.saved_trip_read_mode = False
    st.session_state.planner_step = "review"
    st.session_state.app_workspace = "Plan a trip"


def _show_response_status(record_key: str) -> None:
    """Open a group's response details inside My trips."""

    st.session_state[_OPEN_RESPONSE_STATUS_KEY] = record_key
    st.session_state.pop(_REVIEW_LATEST_RESPONSES_KEY, None)


def _show_latest_response_review(record_key: str) -> None:
    """Open an inline review before rebuilding with newer responses."""

    st.session_state[_OPEN_RESPONSE_STATUS_KEY] = record_key
    st.session_state[_REVIEW_LATEST_RESPONSES_KEY] = record_key


def _close_response_status(record_key: str) -> None:
    """Close response details for the selected My trips record."""

    if st.session_state.get(_OPEN_RESPONSE_STATUS_KEY) == record_key:
        st.session_state.pop(_OPEN_RESPONSE_STATUS_KEY, None)
    if st.session_state.get(_REVIEW_LATEST_RESPONSES_KEY) == record_key:
        st.session_state.pop(_REVIEW_LATEST_RESPONSES_KEY, None)


def _load_preference_drafts(account) -> list[PreferenceDraft]:
    """Load the signed-in organizer's durable group-planning drafts."""

    if account is None:
        return []
    try:
        return list_preference_drafts(account.access_token)
    except Exception:
        st.warning(
            "Group preference drafts are unavailable. Apply the latest Supabase "
            "schema to enable named invitations.",
            icon=":material/group_off:",
        )
        return []


def _load_my_preference_assignments(account) -> list[PreferenceAssignment]:
    """Load preference requests previously claimed by this account."""

    if account is None:
        return []
    try:
        return list_my_preference_assignments(account.access_token)
    except Exception:
        return []


def _open_preference_assignment(assignment: PreferenceAssignment) -> None:
    """Resume a claimed named preference request in the planner."""

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


def _render_my_preference_assignments(
    assignments: list[PreferenceAssignment],
) -> None:
    """Show durable pending preference requests in the traveler account."""

    if not assignments:
        return
    st.markdown("### Trips waiting for your preferences")
    st.caption(
        "These named requests remain available after you sign out or close the link."
    )
    for assignment in assignments:
        with st.container(border=True):
            st.markdown(f"**{assignment.traveler_name}’s preferences**")
            st.caption(
                f"{assignment.trip.destination}, {assignment.trip.country} · "
                f"{assignment.trip.days} days"
            )
            st.button(
                "Complete preferences",
                type="primary",
                icon=":material/edit_note:",
                key=(
                    f"complete-preferences-{assignment.draft_id}-"
                    f"{assignment.slot_id}"
                ),
                on_click=_open_preference_assignment,
                args=(assignment,),
            )


def _render_preference_drafts(drafts: list[PreferenceDraft]) -> None:
    """Show group setups that have not become saved trips yet."""

    if not drafts:
        return
    st.markdown("#### Trips collecting preferences")
    for draft in drafts:
        complete_count = sum(slot.is_complete for slot in draft.slots)
        with st.container(border=True):
            summary_col, action_col = st.columns(
                [4, 1], vertical_alignment="center"
            )
            summary_col.markdown(f"**{draft.title}**")
            summary_col.caption(
                f"{complete_count} of {len(draft.slots)} profiles ready"
            )
            action_col.button(
                "Review",
                key=f"resume-preference-draft-{draft.draft_id}",
                type="primary" if draft.can_build else "secondary",
                on_click=_resume_preference_draft,
                args=(draft,),
            )


def _dismiss_trip_notice(key: str) -> None:
    st.session_state.pop(key, None)


def _render_persistent_trip_notices() -> None:
    """Show save and transfer outcomes until the traveler acknowledges them."""

    if transfer_notice := st.session_state.get(TRIP_TRANSFER_NOTICE_KEY):
        st.success(transfer_notice, icon=":material/cloud_done:")
        st.button(
            "Got it",
            icon=":material/check:",
            key="dismiss-transfer-notice-my-trips",
            on_click=_dismiss_trip_notice,
            args=(TRIP_TRANSFER_NOTICE_KEY,),
        )

    confirmation = st.session_state.get(_SAVED_TRIP_CONFIRMATION_KEY)
    if not isinstance(confirmation, dict):
        return
    title = str(confirmation.get("title") or "Trip")
    destination = (
        "your account"
        if confirmation.get("account_backed")
        else "this active session"
    )
    save_kind = confirmation.get("save_kind")
    st.success(
        (
            f"A new itinerary version is saved to {title}."
            if confirmation.get("collaborator")
            else f"Your working draft is saved to {title}."
            if save_kind == "draft"
            else f"A published itinerary version is saved to {title}."
            if save_kind == "published"
            else f"{title} is saved to {destination}."
        ),
        icon=(
            ":material/cloud_done:"
            if confirmation.get("account_backed")
            else ":material/bookmark_added:"
        ),
    )
    st.button(
        "Dismiss save confirmation",
        icon=":material/close:",
        key="dismiss-save-confirmation-my-trips",
        on_click=_dismiss_trip_notice,
        args=(_SAVED_TRIP_CONFIRMATION_KEY,),
    )


def _render_invitation_notice() -> str | None:
    """Render one invite outcome and return the trip it should select."""

    notice = st.session_state.pop(TRIP_INVITATION_NOTICE_KEY, None)
    if not isinstance(notice, dict):
        return None
    message = str(notice.get("message") or "Trip sharing was updated.")
    if notice.get("level") == "error":
        st.error(message, icon=":material/link_off:")
    else:
        st.success(message, icon=":material/group:")
    return str(notice.get("record_key") or "") or None


def _open_trip_for_edit(record: SavedTrip, version_id: str | None = None) -> None:
    """Restore a saved version in the normal planning workspace for editing."""

    if not record.can_create_itineraries:
        return
    st.session_state.trip_request = record.trip.model_dump(mode="json")
    st.session_state.trip_basics = record.trip.model_dump(mode="json", exclude={"travelers"})
    restored_state = state_for_itinerary_version(
        record.state,
        version_id,
        fallback_updated_at=record.updated_at,
    )
    for key, default in _RESTORED_STATE_DEFAULTS.items():
        st.session_state[key] = restored_state.get(key, default)
    for key, value in restored_state.items():
        st.session_state[key] = value
    st.session_state.saved_trip_id = record.trip_id
    st.session_state.saved_trip_owner_id = record.owner_id
    st.session_state.saved_trip_access_role = record.access_role
    st.session_state.saved_itinerary_version_id = restored_state.get(
        "active_itinerary_version_id"
    )
    st.session_state.active_preference_draft_id = record.preference_draft_id
    st.session_state.saved_trip_read_mode = False
    st.session_state.planner_step = "results"
    st.session_state.app_workspace = "Plan a trip"


def _open_working_draft_for_edit(record: SavedTrip) -> None:
    """Restore the organizer's persisted working draft in the planner."""

    if not record.is_owner:
        return
    st.session_state.trip_request = record.trip.model_dump(mode="json")
    st.session_state.trip_basics = record.trip.model_dump(
        mode="json", exclude={"travelers"}
    )
    restored_state = state_for_working_itinerary_draft(record.state)
    for key, default in _RESTORED_STATE_DEFAULTS.items():
        st.session_state[key] = restored_state.get(key, default)
    for key, value in restored_state.items():
        st.session_state[key] = value
    st.session_state.saved_trip_id = record.trip_id
    st.session_state.saved_trip_owner_id = record.owner_id
    st.session_state.saved_trip_access_role = "owner"
    st.session_state.saved_itinerary_version_id = None
    st.session_state.active_preference_draft_id = record.preference_draft_id
    st.session_state.saved_trip_read_mode = False
    st.session_state.planner_step = "results"
    st.session_state.app_workspace = "Plan a trip"


def _start_new_itinerary(record: SavedTrip) -> None:
    """Open this saved trip's recommendations with no inherited selections."""

    if not record.can_create_itineraries:
        return
    st.session_state.trip_request = record.trip.model_dump(mode="json")
    st.session_state.trip_basics = record.trip.model_dump(
        mode="json",
        exclude={"travelers"},
    )
    st.session_state.traveler_count = len(record.trip.travelers)
    st.session_state.selected_activity_ids = []
    st.session_state.dismissed_must_do_ids = []
    st.session_state.auto_select_must_dos = False
    st.session_state.itinerary_plan = None
    st.session_state.rejected_activities = {}
    st.session_state.itinerary_undo = None
    st.session_state.itinerary_notice = None
    st.session_state.itinerary_narrative = None
    st.session_state.itinerary_narration_error = None
    st.session_state.itinerary_change_proposals = None
    st.session_state.itinerary_change_error = None
    st.session_state.retrieval_cache = None
    st.session_state.activity_detail_id = None
    st.session_state.saved_trip_id = record.trip_id
    st.session_state.saved_trip_owner_id = record.owner_id
    st.session_state.saved_trip_access_role = record.access_role
    st.session_state.saved_itinerary_version_id = None
    st.session_state.active_preference_draft_id = record.preference_draft_id
    st.session_state.saved_trip_read_mode = False
    st.session_state.planner_step = "results"
    st.session_state.app_workspace = "Plan a trip"
    st.session_state.pop("results_view", None)
    st.session_state.pop("itinerary_auto_fill", None)
    _close_saved_itinerary()


def _open_saved_itinerary(record: SavedTrip, version_id: str) -> None:
    """Keep a selected saved itinerary open in the My trips workspace."""

    st.session_state[_OPEN_SAVED_ITINERARY_KEY] = {
        "record_key": record.record_key,
        "version_id": version_id,
    }


def _close_saved_itinerary() -> None:
    st.session_state.pop(_OPEN_SAVED_ITINERARY_KEY, None)


def _version_label(version: dict, position: int) -> str:
    saved_at = str(version.get("saved_at") or "")[:16].replace("T", " ")
    label = str(version.get("label") or f"Itinerary {position + 1}")
    return f"{label} · saved {saved_at} UTC" if saved_at else label


def itinerary_plan_for_version(
    record: SavedTrip, version_id: str
) -> ItineraryPlan | None:
    """Return one saved itinerary as a validated plan for read-only display."""

    restored_state = (
        state_for_working_itinerary_draft(record.state)
        if version_id == WORKING_DRAFT_ID
        else state_for_itinerary_version(
            record.state,
            version_id,
            fallback_updated_at=record.updated_at,
        )
    )
    raw_plan = restored_state.get("itinerary_plan")
    if not raw_plan:
        return None
    status = "draft" if version_id == WORKING_DRAFT_ID else "published"
    return ItineraryPlan.model_validate({**raw_plan, "status": status})


def _hours_label(hours: float) -> str:
    return f"{hours:g} {'hour' if hours == 1 else 'hours'}"


def _render_trip_brief(record: SavedTrip) -> None:
    """Show the saved planning brief without reopening the planner."""

    trip = record.trip
    st.caption(
        f"{trip.destination}, {trip.country} · {trip.days} days · "
        f"{len(trip.travelers)} travelers · {trip.budget_level.value} budget · "
        f"{trip.pace.value} pace"
    )
    if record.is_owner:
        st.caption(
            " · ".join(
                f"{traveler.name}: {', '.join(traveler.interests)}"
                for traveler in trip.travelers
            )
        )
    else:
        group_interests = sorted(
            {
                interest
                for traveler in trip.travelers
                for interest in traveler.interests
            }
        )
        st.caption(f"Group interests: {', '.join(group_interests)}")
    must_do_briefs = [
        f"{traveler.name}: {', '.join(traveler.must_do_activities)}"
        for traveler in trip.travelers
        if traveler.must_do_activities
    ]
    if must_do_briefs and record.is_owner:
        st.caption(f"Must-dos · {' · '.join(must_do_briefs)}")


def _render_owner_sharing(record: SavedTrip) -> None:
    """Let an organizer create a read-only link or revoke shared access."""

    account = current_account_session()
    if account is None or not record.is_owner:
        return
    share_links = st.session_state.setdefault(_SHARE_LINKS_KEY, {})
    if not isinstance(share_links, dict):
        share_links = {}
        st.session_state[_SHARE_LINKS_KEY] = share_links
    origin = current_app_origin()
    with st.popover("Share trip", icon=":material/group_add:"):
        st.markdown("**Invite someone to this trip**")
        st.caption(
            "The link expires in 7 days and the recipient must sign in."
        )
        access_role = "viewer"
        st.caption(
            "Members can view the latest draft and published versions. Only "
            "the organizer can edit or publish."
        )
        if st.button(
            "Create sharing link",
            type="primary",
            icon=":material/link:",
            key=f"create-share-link-{record.record_key}",
            disabled=origin is None,
        ):
            try:
                invitation = create_trip_invitation(
                    record.trip_id,
                    account.user_id,
                    account.access_token,
                    access_role=access_role,
                )
                assert origin is not None
                share_links[record.record_key] = {
                    "url": build_invitation_url(origin, invitation.token),
                    "expires_at": invitation.expires_at,
                    "access_role": invitation.access_role,
                }
            except Exception:
                st.error(
                    "TripSync could not create a sharing link. Apply the Phase 2 "
                    "schema, then try again.",
                    icon=":material/error:",
                )
        if origin is None:
            st.caption("Open TripSync from its normal local or deployed URL to share.")

        link_details = share_links.get(record.record_key)
        if isinstance(link_details, dict) and link_details.get("url"):
            st.caption("Member link")
            st.code(str(link_details["url"]), language=None)
            expires_at = str(link_details.get("expires_at") or "")
            if expires_at:
                st.caption(
                    f"Expires {expires_at[:16].replace('T', ' ')} UTC. "
                    "Use the copy button in the link box."
                )

        st.divider()
        st.caption(
            "Revoke every active link and remove everyone who previously accepted one."
        )
        if st.button(
            "Revoke sharing",
            icon=":material/link_off:",
            key=f"revoke-sharing-{record.record_key}",
        ):
            try:
                result = revoke_trip_sharing(record.trip_id, account.access_token)
            except Exception:
                st.error(
                    "TripSync could not revoke sharing. Try again shortly.",
                    icon=":material/error:",
                )
            else:
                share_links.pop(record.record_key, None)
                removed_members = result["removed_viewers"]
                st.success(
                    (
                        f"Sharing revoked and {removed_members} "
                        f"{'people were' if removed_members != 1 else 'person was'} "
                        "removed."
                    ),
                    icon=":material/check_circle:",
                )


def _leave_viewer_trip(record: SavedTrip) -> None:
    """Let a viewer remove a shared trip from their own account."""

    account = current_account_session()
    if account is None or record.is_owner or not record.owner_id:
        return
    try:
        leave_shared_trip(
            SharedTripIdentity(record.owner_id, record.trip_id),
            account.user_id,
            account.access_token,
        )
    except Exception:
        st.session_state[TRIP_INVITATION_NOTICE_KEY] = {
            "level": "error",
            "message": "TripSync could not remove this shared trip. Try again shortly.",
        }
    else:
        _close_saved_itinerary()
        st.session_state[TRIP_INVITATION_NOTICE_KEY] = {
            "level": "success",
            "message": "The shared trip was removed from My trips.",
        }


def itinerary_comparison_for_versions(
    record: SavedTrip,
    first_version_id: str,
    second_version_id: str,
) -> tuple[dict[str, object], dict[str, object]] | None:
    """Build a compact, read-only comparison for two saved itinerary snapshots."""

    try:
        first_plan = itinerary_plan_for_version(record, first_version_id)
        second_plan = itinerary_plan_for_version(record, second_version_id)
    except ValueError:
        return None
    if first_plan is None or second_plan is None:
        return None

    def summary(plan: ItineraryPlan) -> dict[str, object]:
        activities = [activity for day in plan.days for activity in day.activities]
        names_by_id = {activity.activity_id: activity.activity_name for activity in activities}
        return {
            "activity_count": len(activities),
            "activity_hours": round(sum(day.activity_hours for day in plan.days), 2),
            "planned_hours": round(sum(day.planned_hours for day in plan.days), 2),
            "days": len(plan.days),
            "pace_overrides": sum(
                day.pace_override_approved for day in plan.days
            ),
            "unscheduled_count": len(plan.unscheduled),
            "names_by_id": names_by_id,
        }

    first_summary = summary(first_plan)
    second_summary = summary(second_plan)
    first_ids = set(first_summary["names_by_id"])
    second_ids = set(second_summary["names_by_id"])
    first_summary["only_here"] = [
        first_summary["names_by_id"][activity_id]
        for activity_id in sorted(first_ids - second_ids)
    ]
    second_summary["only_here"] = [
        second_summary["names_by_id"][activity_id]
        for activity_id in sorted(second_ids - first_ids)
    ]
    return first_summary, second_summary


def _render_itinerary_comparison(
    record: SavedTrip,
    versions: list[dict],
) -> None:
    """Let a traveler compare two saved alternatives without leaving My trips."""

    if len(versions) < 2:
        return
    version_ids = [str(version["version_id"]) for version in versions]
    labels = {
        str(version["version_id"]): _version_label(version, position)
        for position, version in enumerate(versions)
    }
    with st.expander("Compare itinerary versions"):
        st.caption("Choose two saved alternatives to see what changes between them.")
        first_column, second_column = st.columns(2)
        first_version_id = first_column.selectbox(
            "First version",
            version_ids,
            index=max(0, len(version_ids) - 2),
            format_func=labels.__getitem__,
            key=f"compare-first-{record.record_key}",
        )
        second_version_id = second_column.selectbox(
            "Second version",
            version_ids,
            index=len(version_ids) - 1,
            format_func=labels.__getitem__,
            key=f"compare-second-{record.record_key}",
        )
        if first_version_id == second_version_id:
            st.info("Choose two different itinerary versions to compare them.", icon=":material/info:")
            return

        comparison = itinerary_comparison_for_versions(
            record,
            first_version_id,
            second_version_id,
        )
        if comparison is None:
            st.info(
                "One of these older itinerary versions cannot be compared yet.",
                icon=":material/info:",
            )
            return

        for column, version_id, summary in zip(
            (first_column, second_column),
            (first_version_id, second_version_id),
            comparison,
            strict=True,
        ):
            with column.container(border=True):
                st.markdown(f"**{labels[version_id]}**")
                st.metric("Activities", summary["activity_count"])
                st.caption(
                    f"{_hours_label(float(summary['activity_hours']))} of activities · "
                    f"{_hours_label(float(summary['planned_hours']))} including transitions"
                )
                st.caption(
                    f"{summary['days']} days · {summary['unscheduled_count']} unscheduled · "
                    f"{summary['pace_overrides']} pace override(s)"
                )
                only_here = summary["only_here"]
                if only_here:
                    st.markdown("**Only in this version**")
                    st.caption(" · ".join(str(name) for name in only_here))
                else:
                    st.caption("No activities are unique to this version.")


def _render_itinerary_alternative_editor(
    record: SavedTrip,
    version: dict,
    position: int,
    plan: ItineraryPlan,
) -> None:
    """Save a named variant without altering the source itinerary snapshot."""
    if not record.can_create_itineraries:
        return
    version_id = str(version["version_id"])
    activity_locations = [
        (day.day_number, activity)
        for day in plan.days
        for activity in day.activities
    ]
    if not activity_locations:
        st.caption("This itinerary has no stops to revise yet.")
        return

    day_numbers = [day.day_number for day in plan.days]
    activity_labels = {
        activity.activity_id: f"{activity.activity_name} · Day {day_number}"
        for day_number, activity in activity_locations
    }
    default_label = f"{version.get('label') or f'Itinerary {position + 1}'} alternative"

    with st.expander("Create itinerary alternative"):
        st.caption(
            "The itinerary above stays unchanged. Remove or move stops, then save a new "
            "version to compare with it."
        )
        with st.form(f"itinerary-alternative-form-{record.record_key}-{version_id}"):
            label = st.text_input(
                "Alternative name", value=default_label, max_chars=80
            )
            removed_ids = st.multiselect(
                "Remove stops",
                options=list(activity_labels),
                format_func=activity_labels.__getitem__,
                placeholder="Keep every stop",
            )
            removed_id_set = set(removed_ids)
            remaining_activities = [
                (day_number, activity)
                for day_number, activity in activity_locations
                if activity.activity_id not in removed_id_set
            ]
            if remaining_activities:
                st.caption("Move a stop to a different day if you want to try another balance.")

            target_days: dict[str, int] = {}
            moved_activity_ids: set[str] = set()
            for original_day, activity in remaining_activities:
                target_days[activity.activity_id] = st.selectbox(
                    f"Day for {activity.activity_name}",
                    options=day_numbers,
                    index=day_numbers.index(original_day),
                    format_func=lambda day_number: f"Day {day_number}",
                    key=(
                        f"alternative-day-{record.record_key}-{version_id}-"
                        f"{activity.activity_id}"
                    ),
                )
                if target_days[activity.activity_id] != original_day:
                    moved_activity_ids.add(activity.activity_id)

            pace_override_approved = st.checkbox(
                "I understand this alternative may exceed the recommended pace. Save it anyway."
            )
            submitted = st.form_submit_button(
                "Save itinerary alternative",
                type="primary",
                icon=":material/bookmark_add:",
                width="stretch",
            )

        if not submitted:
            return
        if not removed_id_set and not moved_activity_ids:
            st.info(
                "Remove or move at least one stop before saving an alternative.",
                icon=":material/info:",
            )
            return

        try:
            revised_plan = revise_itinerary_plan(
                plan,
                remove_activity_ids=removed_ids,
                target_day_by_activity_id=target_days,
                allow_pace_override=pace_override_approved,
            )
        except ValueError as error:
            st.error(str(error), icon=":material/error:")
            return

        source_state = state_for_itinerary_version(record.state, version_id=version_id)
        source_state["itinerary_plan"] = revised_plan.model_dump(mode="json")
        source_state["itinerary_narrative"] = None
        source_state["selected_activity_ids"] = [
            activity_id
            for activity_id in source_state.get("selected_activity_ids", [])
            if activity_id not in removed_id_set
        ]
        access_token = str(
            (st.session_state.get("account_session") or {}).get("access_token")
            or ""
        )
        saved_record = save_trip(
            record.trip,
            source_state,
            trip_id=record.trip_id,
            session_id=st.session_state.feedback_session_id,
            save_itinerary_version=True,
            itinerary_label=label,
            force_new_itinerary_version=True,
            auth_access_token=access_token,
            owner_id=record.owner_id,
        )
        saved_version_id = str(
            saved_record.state.get("active_itinerary_version_id", version_id)
        )
        _open_saved_itinerary(saved_record, saved_version_id)
        st.session_state[_SAVED_ITINERARY_FLASH_KEY] = (
            f"Saved {label.strip() or 'a new itinerary alternative'}."
        )
        st.rerun()


def _render_saved_itinerary(
    record: SavedTrip,
    version: dict,
    position: int,
    *,
    is_working_draft: bool = False,
) -> None:
    """Show a saved itinerary below its trip card without returning to the planner."""

    version_id = str(version["version_id"])
    try:
        plan = itinerary_plan_for_version(record, version_id)
    except ValueError:
        st.error(
            "This saved itinerary uses an older format and cannot be displayed yet. "
            + (
                "Open it in the planner to update it."
                if record.can_create_itineraries
                else "The owner must resave it before it can be displayed."
            ),
            icon=":material/error:",
        )
        if record.can_create_itineraries:
            st.button(
                "Open in planner",
                icon=":material/edit:",
                key=f"edit-legacy-itinerary-{record.record_key}-{version_id}",
                on_click=_open_trip_for_edit,
                args=(record, version_id),
            )
        return

    if plan is None:
        st.info("This saved trip does not include an itinerary yet.", icon=":material/info:")
        return

    activity_count = sum(len(day.activities) for day in plan.days)
    activity_hours = sum(day.activity_hours for day in plan.days)
    with st.container(border=True):
        st.markdown(
            '<div class="ts-section-label">Working draft</div>'
            if is_working_draft
            else '<div class="ts-section-label">Saved itinerary</div>',
            unsafe_allow_html=True,
        )
        st.subheader(_version_label(version, position))
        st.caption(
            f"{activity_count} activities · {_hours_label(activity_hours)} of activities · "
            f"{plan.pace.value} pace"
        )
        if is_working_draft:
            st.caption(
                "This is the organizer’s latest editable draft."
                if record.is_owner
                else "This is the organizer’s latest draft. It is read-only for members."
            )
        else:
            st.caption(
                "This is a shared read-only snapshot."
                if not record.is_owner
                else (
                    "This is a saved snapshot. Create an alternative here, or edit "
                    "matched attractions in the planner."
                )
            )

        for day in plan.days:
            with st.container(border=True):
                heading, hours = st.columns([3, 2])
                heading.markdown(f"#### Day {day.day_number}")
                hours.caption(
                    f"{_hours_label(day.planned_hours)} of "
                    f"{_hours_label(day.capacity_hours)} planned"
                )
                if day.pace_override_approved:
                    st.warning("This day exceeds its recommended pace.", icon=":material/schedule:")
                estimate = day.budget_estimate
                if estimate is not None:
                    st.markdown(
                        "**Estimated daily budget per person: "
                        f"{euro_range(estimate.total_min_eur, estimate.total_max_eur)}**"
                    )
                    st.caption(
                        "Activities "
                        f"{euro_range(estimate.activity_min_eur, estimate.activity_max_eur)}"
                        " · Meals and snacks "
                        f"{euro_range(estimate.food_min_eur, estimate.food_max_eur)}"
                    )
                    if estimate.potentially_over_budget:
                        st.warning(
                            "The upper estimate is above the group’s "
                            f"{daily_budget_label(estimate.target_budget_level)} "
                            "daily band. This is advisory.",
                            icon=":material/account_balance_wallet:",
                        )
                for index, activity in enumerate(day.activities):
                    slot = itinerary_time_block_label(
                        activity,
                        index,
                        len(day.activities),
                    )
                    st.markdown(f"**{slot} · {activity.activity_name}**")
                    metadata = [
                        _hours_label(activity.duration_hours),
                        "Your shortlist" if activity.source.value == "shortlist" else "Group recommendation",
                    ]
                    if activity.traveler_names and record.is_owner:
                        metadata.append(f"Serves {' + '.join(activity.traveler_names)}")
                    st.caption(" · ".join(metadata))
                    if activity.must_do_owners and record.is_owner:
                        st.caption(f"Must-do for {' + '.join(activity.must_do_owners)}")
                    elif activity.must_do_owners:
                        st.caption("Group must-do")
                    st.write(
                        activity.reason
                        if record.is_owner
                        else "Included for the group’s shared preferences."
                    )
        if plan.unscheduled:
            with st.expander(f"Not scheduled ({len(plan.unscheduled)})"):
                for activity in plan.unscheduled:
                    st.markdown(f"**{activity.activity_name}** — {activity.reason}")

        if record.can_create_itineraries and not is_working_draft:
            _render_itinerary_alternative_editor(record, version, position, plan)

        with st.container(horizontal=True):
            if is_working_draft and record.is_owner:
                st.button(
                    "Continue editing",
                    icon=":material/edit:",
                    key=f"edit-working-draft-{record.record_key}",
                    on_click=_open_working_draft_for_edit,
                    args=(record,),
                )
            elif record.can_create_itineraries:
                st.button(
                    "Edit recommendations",
                    icon=":material/edit:",
                    key=f"edit-saved-itinerary-{record.record_key}-{version_id}",
                    on_click=_open_trip_for_edit,
                    args=(record, version_id),
                )
            st.button(
                "Close itinerary",
                icon=":material/close:",
                key=f"close-saved-itinerary-{record.record_key}-{version_id}",
                on_click=_close_saved_itinerary,
            )


def _legacy_group_matches(
    records: list[SavedTrip],
    drafts: list[PreferenceDraft],
) -> dict[str, PreferenceDraft]:
    """Find unambiguous exact matches saved before draft origins were recorded."""

    explicitly_linked_ids = {
        record.preference_draft_id
        for record in records
        if record.preference_draft_id is not None
    }
    candidates = [
        record
        for record in records
        if record.is_owner and record.preference_draft_id is None
    ]
    matches: dict[str, PreferenceDraft] = {}
    for draft in drafts:
        if not draft.is_ready or draft.draft_id in explicitly_linked_ids:
            continue
        matching_records = [
            record
            for record in candidates
            if record.trip == draft.to_trip_request()
            and record.record_key not in matches
        ]
        if len(matching_records) == 1:
            matches[matching_records[0].record_key] = draft
    return matches

 
def _trip_option_label(
    record: SavedTrip, version_count: int, *, has_working_draft: bool = False
) -> str:
    noun = "itinerary" if version_count == 1 else "itineraries"
    sharing = (
        " · shared with you"
        if not record.is_owner
        else " · planned together"
        if record.preference_draft_id
        else ""
    )
    saved_at = record.updated_at[:16].replace("T", " ")
    saved_label = f" · saved {saved_at} UTC" if saved_at else ""
    draft_label = " · working draft" if has_working_draft else ""
    return (
        f"{record.title} · {version_count} saved {noun}"
        f"{draft_label}{saved_label}{sharing}"
    )


def _render_saved_trip_collection(
    records: list[SavedTrip],
    *,
    content_mode: Literal["drafts", "published"],
    selector_label: str,
    selector_key: str,
    invited_record_key: str | None = None,
    legacy_matches: dict[str, PreferenceDraft] | None = None,
    preference_drafts_by_id: dict[str, PreferenceDraft] | None = None,
    account=None,
) -> None:
    """Render one independently selectable group of saved trips."""

    if not records:
        st.caption("No saved trips in this section yet.")
        return
    legacy_matches = legacy_matches or {}
    preference_drafts_by_id = preference_drafts_by_id or {}
    records_by_key = {record.record_key: record for record in records}
    versions_by_record_key = {
        record.record_key: itinerary_versions(
            record.state,
            fallback_updated_at=record.updated_at,
        )
        for record in records
    }
    drafts_by_record_key = {
        record.record_key: working_itinerary_draft(record.state)
        for record in records
    }
    trip_labels = {
        record.record_key: _trip_option_label(
            record,
            len(versions_by_record_key[record.record_key]),
            has_working_draft=drafts_by_record_key[record.record_key] is not None,
        )
        for record in records
    }
    if invited_record_key in records_by_key:
        st.session_state[selector_key] = invited_record_key
    selected_record_key = st.selectbox(
        selector_label,
        options=list(records_by_key),
        format_func=trip_labels.__getitem__,
        key=selector_key,
        persist_state="session",
    )
    record = records_by_key[selected_record_key]
    open_itinerary = st.session_state.get(_OPEN_SAVED_ITINERARY_KEY, {})
    versions = (
        versions_by_record_key[record.record_key]
        if content_mode == "published"
        else []
    )
    working_draft = (
        drafts_by_record_key[record.record_key]
        if content_mode == "drafts"
        else None
    )
    version_ids = [version["version_id"] for version in versions]
    version_labels = {
        version["version_id"]: _version_label(version, position)
        for position, version in enumerate(versions)
    }

    with st.container(border=True):
        st.subheader(record.title)
        st.caption(f"Last saved {record.updated_at[:16].replace('T', ' ')} UTC")
        if not record.is_owner:
            st.info(
                "Shared with you · read-only. You can view the latest draft and "
                "published itineraries, while only the organizer can make changes.",
                icon=":material/visibility:",
            )
        elif record.preference_draft_id:
            st.info(
                "Planned together · this trip was built from named traveler "
                "preferences.",
                icon=":material/groups:",
            )
        elif draft := legacy_matches.get(record.record_key):
            st.info(
                "This trip exactly matches a completed group draft created before "
                "TripSync started recording origins.",
                icon=":material/join_inner:",
            )
            if st.button(
                "Confirm group link",
                key=f"confirm-group-link-{record.record_key}",
                icon=":material/link:",
            ):
                try:
                    if account is None:
                        raise ValueError("Sign in to link this group trip")
                    link_saved_trip_to_preference_draft(
                        record,
                        draft,
                        account.access_token,
                    )
                except Exception:
                    st.error(
                        "TripSync could not link this saved trip to its group draft.",
                        icon=":material/error:",
                    )
                else:
                    st.session_state[_SAVED_ITINERARY_FLASH_KEY] = (
                        f"Linked {record.title} to its group planning history."
                    )
                    st.rerun()
        _render_trip_brief(record)
        preference_draft = preference_drafts_by_id.get(
            record.preference_draft_id or ""
        )
        if record.is_owner and preference_draft is not None:
            complete_count = sum(
                slot.is_complete for slot in preference_draft.slots
            )
            st.caption(
                f"Preference responses: {complete_count} of "
                f"{len(preference_draft.slots)} ready · this itinerary uses "
                f"{len(record.trip.travelers)} profiles"
            )
            latest_trip = (
                preference_draft.to_trip_request()
                if preference_draft.can_build
                else None
            )
            if latest_trip is not None and latest_trip != record.trip:
                st.info(
                    "New or updated traveler responses are available. Your "
                    "existing draft has not changed.",
                    icon=":material/mark_email_unread:",
                )
                st.button(
                    "Review latest responses",
                    key=(
                        f"review-latest-responses-{content_mode}-"
                        f"{record.record_key}"
                    ),
                    icon=":material/rate_review:",
                    on_click=_show_latest_response_review,
                    args=(record.record_key,),
                )
        can_start_new = record.can_create_itineraries and (
            content_mode == "published" or working_draft is None
        )
        if record.is_owner:
            with st.container(horizontal=True):
                if can_start_new:
                    st.button(
                        "Create new itinerary",
                        icon=":material/add_circle:",
                        type="primary",
                        key=f"new-itinerary-{record.record_key}",
                        on_click=_start_new_itinerary,
                        args=(record,),
                    )
                if preference_draft is not None:
                    st.button(
                        "View response status",
                        icon=":material/group:",
                        key=(
                            f"view-response-status-{content_mode}-"
                            f"{record.record_key}"
                        ),
                        on_click=_show_response_status,
                        args=(record.record_key,),
                    )
                _render_owner_sharing(record)
            if (
                preference_draft is not None
                and st.session_state.get(_OPEN_RESPONSE_STATUS_KEY)
                == record.record_key
            ):
                with st.container(border=True):
                    with st.container(horizontal=True):
                        reviewing_latest = (
                            st.session_state.get(_REVIEW_LATEST_RESPONSES_KEY)
                            == record.record_key
                        )
                        st.markdown(
                            "#### Review latest responses"
                            if reviewing_latest
                            else "#### Traveler response status"
                        )
                        st.button(
                            "Close",
                            icon=":material/close:",
                            key=(
                                f"close-response-status-{content_mode}-"
                                f"{record.record_key}"
                            ),
                            on_click=_close_response_status,
                            args=(record.record_key,),
                        )
                    render_preference_draft_status(
                        preference_draft,
                        key_prefix=(
                            f"my-trips-response-status-{content_mode}-"
                            f"{record.record_key}"
                        ),
                    )
                    if reviewing_latest:
                        latest_names = [
                            slot.traveler_name
                            for slot in preference_draft.slots
                            if slot.is_complete
                        ]
                        st.info(
                            "Your saved draft remains unchanged until you choose "
                            "to update recommendations. The next recommendation "
                            f"set will use {len(latest_names)} profiles: "
                            f"{', '.join(latest_names)}.",
                            icon=":material/info:",
                        )
                        st.button(
                            "Update recommendations",
                            type="primary",
                            icon=":material/auto_awesome:",
                            key=(
                                f"update-recommendations-{content_mode}-"
                                f"{record.record_key}"
                            ),
                            on_click=_resume_preference_draft,
                            args=(preference_draft, record),
                        )
        if can_start_new:
            st.caption(
                "Start from this trip's curated activities with an empty shortlist."
            )
        elif not record.can_create_itineraries:
            if st.button(
                "Remove from My trips",
                icon=":material/person_remove:",
                key=f"leave-shared-trip-{record.record_key}",
                on_click=_leave_viewer_trip,
                args=(record,),
            ):
                st.rerun()

        if working_draft is not None:
            st.markdown("#### Working draft")
            st.caption(
                "Editable by the organizer · saved "
                f"{str(working_draft.get('saved_at') or '')[:16].replace('T', ' ')} UTC"
            )
            with st.container(horizontal=True):
                st.button(
                    "View draft",
                    icon=":material/visibility:",
                    key=f"view-working-draft-{record.record_key}",
                    on_click=_open_saved_itinerary,
                    args=(record, WORKING_DRAFT_ID),
                )
                if record.is_owner:
                    st.button(
                        "Continue editing",
                        icon=":material/edit:",
                        key=f"continue-working-draft-{record.record_key}",
                        on_click=_open_working_draft_for_edit,
                        args=(record,),
                    )

        if versions:
            st.markdown("#### Saved itineraries")
            st.caption(
                f"{len(versions)} saved itinerary "
                f"{'version' if len(versions) == 1 else 'versions'} for this trip"
            )
            selected_version_id = st.selectbox(
                "Choose an itinerary",
                version_ids,
                index=len(version_ids) - 1,
                format_func=version_labels.__getitem__,
                key=(
                    f"saved-version-{record.record_key}-"
                    f"{versions[-1]['version_id']}"
                ),
                persist_state="session",
            )
            with st.container(horizontal=True):
                st.button(
                    "Open itinerary",
                    icon=":material/folder_open:",
                    key=f"open-{record.record_key}",
                    on_click=_open_saved_itinerary,
                    args=(record, selected_version_id),
                )
                if record.can_create_itineraries:
                    st.button(
                        "Edit recommendations",
                        icon=":material/edit:",
                        key=f"edit-recommendations-{record.record_key}",
                        on_click=_open_trip_for_edit,
                        args=(record, selected_version_id),
                    )
            _render_itinerary_comparison(record, versions)
        else:
            st.caption("No published itineraries have been saved for this trip yet.")

    if open_itinerary.get("record_key") != record.record_key:
        return

    active_version_id = str(open_itinerary.get("version_id") or "")
    if (
        content_mode == "drafts" and active_version_id != WORKING_DRAFT_ID
    ) or (
        content_mode == "published" and active_version_id == WORKING_DRAFT_ID
    ):
        return
    active_version = (
        working_draft
        if active_version_id == WORKING_DRAFT_ID
        else next(
            (
                version
                for version in versions
                if version["version_id"] == active_version_id
            ),
            None,
        )
    )
    if active_version is None:
        st.info("This saved itinerary is no longer available.", icon=":material/info:")
    else:
        _render_saved_itinerary(
            record,
            active_version,
            -1 if active_version_id == WORKING_DRAFT_ID else version_ids.index(active_version_id),
            is_working_draft=active_version_id == WORKING_DRAFT_ID,
        )


def _render_saved_trip_sections(
    *,
    content_mode: Literal["drafts", "published"],
    group_records: list[SavedTrip],
    self_records: list[SavedTrip],
    active_preference_drafts: list[PreferenceDraft],
    invited_record_key: str | None,
    legacy_matches: dict[str, PreferenceDraft],
    preference_drafts_by_id: dict[str, PreferenceDraft],
    account,
) -> None:
    """Render Group/Self organization inside one lifecycle tab."""

    def belongs(record: SavedTrip) -> bool:
        versions = itinerary_versions(
            record.state,
            fallback_updated_at=record.updated_at,
        )
        if content_mode == "published":
            return bool(versions)
        return working_itinerary_draft(record.state) is not None or not versions

    visible_group_records = [record for record in group_records if belongs(record)]
    visible_self_records = [record for record in self_records if belongs(record)]
    shown_preference_drafts = (
        active_preference_drafts if content_mode == "drafts" else []
    )
    group_count = len(shown_preference_drafts) + len(visible_group_records)
    selector_prefix = "" if content_mode == "drafts" else "published-"

    with st.expander(
        f"Group planning ({group_count})",
        expanded=bool(group_count),
    ):
        st.caption(
            "Trips collecting named preferences and itineraries planned with "
            "other people."
        )
        _render_preference_drafts(shown_preference_drafts)
        if shown_preference_drafts and visible_group_records:
            st.divider()
        if visible_group_records:
            st.markdown("#### Group trips and itineraries")
        _render_saved_trip_collection(
            visible_group_records,
            content_mode=content_mode,
            selector_label="Choose a group trip",
            selector_key=f"{selector_prefix}group-trip-selector",
            invited_record_key=invited_record_key,
            legacy_matches=legacy_matches,
            preference_drafts_by_id=preference_drafts_by_id,
            account=account,
        )

    with st.expander(
        f"Self planning ({len(visible_self_records)})",
        expanded=not group_count,
    ):
        st.caption(
            "Trips you planned by entering everyone’s preferences yourself."
        )
        _render_saved_trip_collection(
            visible_self_records,
            content_mode=content_mode,
            selector_label="Choose a self-planned trip",
            selector_key=f"{selector_prefix}self-trip-selector",
            invited_record_key=invited_record_key,
            account=account,
        )


def render_saved_trips() -> None:
    st.markdown(
        '<div class="ts-section-label">Your saved plans</div>',
        unsafe_allow_html=True,
    )
    st.title("Pick up where you left off")
    account = current_account_session()
    st.caption(
        "Signed-in plans are private and follow your account across devices."
        if account
        else "Plans in this active session stay private. Sign in to keep them "
        "across sessions and devices."
    )
    _render_persistent_trip_notices()
    invited_record_key = _render_invitation_notice()
    if notice := st.session_state.pop(_SAVED_ITINERARY_FLASH_KEY, None):
        st.toast(notice, icon=":material/check_circle:")

    drafts = _load_preference_drafts(account)
    pending_assignments = [
        assignment
        for assignment in _load_my_preference_assignments(account)
        if assignment.profile is None
    ]
    records = list_saved_trips(
        st.session_state.feedback_session_id,
        auth_access_token=(account.access_token if account else None),
    )
    legacy_matches = _legacy_group_matches(records, drafts)
    group_records = [
        record
        for record in records
        if record.is_group_plan or record.record_key in legacy_matches
    ]
    self_records = [
        record
        for record in records
        if record not in group_records
    ]
    represented_draft_ids = {
        draft_id
        for record in group_records
        if (draft_id := record.preference_draft_id)
    } | {draft.draft_id for draft in legacy_matches.values()}
    active_drafts = [
        draft for draft in drafts if draft.draft_id not in represented_draft_ids
    ]

    draft_count = len(pending_assignments) + len(active_drafts) + sum(
        working_itinerary_draft(record.state) is not None
        or not itinerary_versions(
            record.state,
            fallback_updated_at=record.updated_at,
        )
        for record in records
    )
    published_count = sum(
        len(
            itinerary_versions(
                record.state,
                fallback_updated_at=record.updated_at,
            )
        )
        for record in records
    )
    draft_label = f"Drafts ({draft_count})"
    published_label = f"Published ({published_count})"
    draft_tab, published_tab = st.tabs(
        [draft_label, published_label],
        default=draft_label if draft_count else published_label,
        key="my-trips-status-tab",
        on_change="rerun",
    )
    if draft_tab.open:
        with draft_tab:
            _render_my_preference_assignments(pending_assignments)
            _render_saved_trip_sections(
                content_mode="drafts",
                group_records=group_records,
                self_records=self_records,
                active_preference_drafts=active_drafts,
                invited_record_key=invited_record_key,
                legacy_matches=legacy_matches,
                preference_drafts_by_id={draft.draft_id: draft for draft in drafts},
                account=account,
            )
    if published_tab.open:
        with published_tab:
            _render_saved_trip_sections(
                content_mode="published",
                group_records=group_records,
                self_records=self_records,
                active_preference_drafts=active_drafts,
                invited_record_key=invited_record_key,
                legacy_matches=legacy_matches,
                preference_drafts_by_id={draft.draft_id: draft for draft in drafts},
                account=account,
            )

    if not records and not drafts:
        st.info(
            "Save a plan from the results screen and it will appear here. "
            + (
                "Plans saved in another tab or expired session appear after you "
                "sign in."
                if account is None
                else "No plans are currently saved to this account."
            ),
            icon=":material/bookmark:",
        )
