"""Saved-trip browser for the Streamlit workspace."""

from __future__ import annotations

import streamlit as st

from src.auth_ui import TRIP_TRANSFER_NOTICE_KEY, current_account_session
from src.models import ItineraryPlan
from src.trips import (
    SavedTrip,
    itinerary_versions,
    list_saved_trips,
    revise_itinerary_plan,
    save_trip,
    state_for_itinerary_version,
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
    st.success(
        f"{title} is saved to {destination}.",
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


def _open_trip_for_edit(record: SavedTrip, version_id: str | None = None) -> None:
    """Restore a saved version in the normal planning workspace for editing."""

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
    st.session_state.saved_itinerary_version_id = restored_state.get(
        "active_itinerary_version_id"
    )
    st.session_state.saved_trip_read_mode = False
    st.session_state.planner_step = "results"
    st.session_state.app_workspace = "Plan a trip"


def _start_new_itinerary(record: SavedTrip) -> None:
    """Open this saved trip's recommendations with no inherited selections."""

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
    st.session_state.saved_itinerary_version_id = None
    st.session_state.saved_trip_read_mode = False
    st.session_state.planner_step = "results"
    st.session_state.app_workspace = "Plan a trip"
    st.session_state.pop("results_view", None)
    st.session_state.pop("itinerary_auto_fill", None)
    _close_saved_itinerary()


def _open_saved_itinerary(record: SavedTrip, version_id: str) -> None:
    """Keep a selected saved itinerary open in the My trips workspace."""

    st.session_state[_OPEN_SAVED_ITINERARY_KEY] = {
        "trip_id": record.trip_id,
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

    restored_state = state_for_itinerary_version(
        record.state,
        version_id,
        fallback_updated_at=record.updated_at,
    )
    raw_plan = restored_state.get("itinerary_plan")
    return ItineraryPlan.model_validate(raw_plan) if raw_plan else None


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
    st.caption(
        " · ".join(
            f"{traveler.name}: {', '.join(traveler.interests)}"
            for traveler in trip.travelers
        )
    )
    must_do_briefs = [
        f"{traveler.name}: {', '.join(traveler.must_do_activities)}"
        for traveler in trip.travelers
        if traveler.must_do_activities
    ]
    if must_do_briefs:
        st.caption(f"Must-dos · {' · '.join(must_do_briefs)}")


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
            key=f"compare-first-{record.trip_id}",
        )
        second_version_id = second_column.selectbox(
            "Second version",
            version_ids,
            index=len(version_ids) - 1,
            format_func=labels.__getitem__,
            key=f"compare-second-{record.trip_id}",
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
        with st.form(f"itinerary-alternative-form-{record.trip_id}-{version_id}"):
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
                        f"alternative-day-{record.trip_id}-{version_id}-"
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
        saved_record = save_trip(
            record.trip,
            source_state,
            trip_id=record.trip_id,
            session_id=st.session_state.feedback_session_id,
            save_itinerary_version=True,
            itinerary_label=label,
            force_new_itinerary_version=True,
            auth_access_token=(
                (st.session_state.get("account_session") or {}).get("access_token")
            ),
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


def _render_saved_itinerary(record: SavedTrip, version: dict, position: int) -> None:
    """Show a saved itinerary below its trip card without returning to the planner."""

    version_id = str(version["version_id"])
    try:
        plan = itinerary_plan_for_version(record, version_id)
    except ValueError:
        st.error(
            "This saved itinerary uses an older format and cannot be displayed yet. "
            "Open it in the planner to update it.",
            icon=":material/error:",
        )
        st.button(
            "Open in planner",
            icon=":material/edit:",
            key=f"edit-legacy-itinerary-{record.trip_id}-{version_id}",
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
        st.markdown('<div class="ts-section-label">Saved itinerary</div>', unsafe_allow_html=True)
        st.subheader(_version_label(version, position))
        st.caption(
            f"{activity_count} activities · {_hours_label(activity_hours)} of activities · "
            f"{plan.pace.value} pace"
        )
        st.caption(
            "This is a saved snapshot. Create an alternative here, or edit matched attractions in the planner."
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
                for index, activity in enumerate(day.activities):
                    slot = ("Morning", "Midday", "Afternoon", "Evening")[min(index, 3)]
                    st.markdown(f"**{slot} · {activity.activity_name}**")
                    metadata = [
                        _hours_label(activity.duration_hours),
                        "Your shortlist" if activity.source.value == "shortlist" else "Group recommendation",
                    ]
                    if activity.traveler_names:
                        metadata.append(f"Serves {' + '.join(activity.traveler_names)}")
                    st.caption(" · ".join(metadata))
                    if activity.must_do_owners:
                        st.caption(f"Must-do for {' + '.join(activity.must_do_owners)}")
                    st.write(activity.reason)
        if plan.unscheduled:
            with st.expander(f"Not scheduled ({len(plan.unscheduled)})"):
                for activity in plan.unscheduled:
                    st.markdown(f"**{activity.activity_name}** — {activity.reason}")

        _render_itinerary_alternative_editor(record, version, position, plan)

        with st.container(horizontal=True):
            st.button(
                "Edit recommendations",
                icon=":material/edit:",
                key=f"edit-saved-itinerary-{record.trip_id}-{version_id}",
                on_click=_open_trip_for_edit,
                args=(record, version_id),
            )
            st.button(
                "Close itinerary",
                icon=":material/close:",
                key=f"close-saved-itinerary-{record.trip_id}-{version_id}",
                on_click=_close_saved_itinerary,
            )


def render_saved_trips() -> None:
    st.markdown('<div class="ts-section-label">Your saved plans</div>', unsafe_allow_html=True)
    st.title("Pick up where you left off")
    account = current_account_session()
    st.caption(
        "Signed-in plans are private and follow your account across devices."
        if account
        else "Plans in this active session stay private. Sign in to keep them across sessions and devices."
    )
    _render_persistent_trip_notices()
    if notice := st.session_state.pop(_SAVED_ITINERARY_FLASH_KEY, None):
        st.toast(notice, icon=":material/check_circle:")
    records = list_saved_trips(
        st.session_state.feedback_session_id,
        auth_access_token=(account.access_token if account else None),
    )
    if not records:
        st.info(
            "Save a plan from the results screen and it will appear here. "
            + (
                "Plans saved in another tab or expired session appear after you sign in."
                if account is None
                else "No plans are currently saved to this account."
            ),
            icon=":material/bookmark:",
        )
        return

    records_by_id = {record.trip_id: record for record in records}
    versions_by_trip_id = {
        record.trip_id: itinerary_versions(
            record.state,
            fallback_updated_at=record.updated_at,
        )
        for record in records
    }
    trip_labels = {
        record.trip_id: (
            f"{record.title} · {len(versions_by_trip_id[record.trip_id])} saved "
            f"{'itinerary' if len(versions_by_trip_id[record.trip_id]) == 1 else 'itineraries'}"
        )
        for record in records
    }
    selected_trip_id = st.selectbox(
        "Choose a trip",
        options=list(records_by_id),
        format_func=trip_labels.__getitem__,
        key="saved-trip-selector",
        persist_state="session",
    )
    record = records_by_id[selected_trip_id]
    open_itinerary = st.session_state.get(_OPEN_SAVED_ITINERARY_KEY, {})
    versions = versions_by_trip_id[record.trip_id]
    version_ids = [version["version_id"] for version in versions]
    version_labels = {
        version["version_id"]: _version_label(version, position)
        for position, version in enumerate(versions)
    }

    with st.container(border=True):
        st.subheader(record.title)
        st.caption(f"Last saved {record.updated_at[:16].replace('T', ' ')} UTC")
        _render_trip_brief(record)
        st.button(
            "Create new itinerary",
            icon=":material/add_circle:",
            type="primary",
            key=f"new-itinerary-{record.trip_id}",
            on_click=_start_new_itinerary,
            args=(record,),
        )
        st.caption(
            "Start from this trip's curated activities with an empty shortlist."
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
                    f"saved-version-{record.trip_id}-"
                    f"{versions[-1]['version_id']}"
                ),
                persist_state="session",
            )
            with st.container(horizontal=True):
                st.button(
                    "Open itinerary",
                    icon=":material/folder_open:",
                    key=f"open-{record.trip_id}",
                    on_click=_open_saved_itinerary,
                    args=(record, selected_version_id),
                )
                st.button(
                    "Edit recommendations",
                    icon=":material/edit:",
                    key=f"edit-recommendations-{record.trip_id}",
                    on_click=_open_trip_for_edit,
                    args=(record, selected_version_id),
                )
            _render_itinerary_comparison(record, versions)
        else:
            st.caption("No itineraries have been saved for this trip yet.")

    if open_itinerary.get("trip_id") != record.trip_id:
        return

    active_version_id = str(open_itinerary.get("version_id") or "")
    active_version = next(
        (
            version
            for version in versions
            if version["version_id"] == active_version_id
        ),
        None,
    )
    if active_version is None:
        st.info("This saved itinerary is no longer available.", icon=":material/info:")
    else:
        _render_saved_itinerary(
            record,
            active_version,
            version_ids.index(active_version_id),
        )
