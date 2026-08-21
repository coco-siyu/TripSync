"""Saved-trip browser for the Streamlit workspace."""

from __future__ import annotations

import streamlit as st

from src.models import ItineraryPlan
from src.trips import (
    SavedTrip,
    itinerary_versions,
    list_saved_trips,
    state_for_itinerary_version,
)


_RESTORED_STATE_DEFAULTS = {
    "selected_activity_ids": [],
    "dismissed_must_do_ids": [],
    "itinerary_plan": None,
    "rejected_activities": {},
    "itinerary_narrative": None,
}
_OPEN_SAVED_ITINERARY_KEY = "open_saved_itinerary"


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
    st.session_state.saved_itinerary_version_id = restored_state.get(
        "active_itinerary_version_id"
    )
    st.session_state.saved_trip_read_mode = False
    st.session_state.planner_step = "results"
    st.session_state.app_workspace = "Plan a trip"


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
            "This is a saved snapshot. Edit matched attractions to make a new itinerary version."
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

        edit, close = st.columns(2)
        edit.button(
            "Edit matched attractions",
            icon=":material/edit:",
            key=f"edit-saved-itinerary-{record.trip_id}-{version_id}",
            on_click=_open_trip_for_edit,
            args=(record, version_id),
            use_container_width=True,
        )
        close.button(
            "Close itinerary",
            icon=":material/close:",
            key=f"close-saved-itinerary-{record.trip_id}-{version_id}",
            on_click=_close_saved_itinerary,
            use_container_width=True,
        )


def render_saved_trips() -> None:
    st.markdown('<div class="ts-section-label">Your saved plans</div>', unsafe_allow_html=True)
    st.title("Pick up where you left off")
    st.caption(
        "Saved trips include the itinerary you had at the time of saving. This "
        "demo shows plans saved in this browser session; other visitors' plans stay private."
    )
    records = list_saved_trips(st.session_state.feedback_session_id)
    if not records:
        st.info("Save a plan from the results screen and it will appear here.", icon=":material/bookmark:")
        return
    open_itinerary = st.session_state.get(_OPEN_SAVED_ITINERARY_KEY, {})
    for record in records:
        versions = itinerary_versions(record.state, fallback_updated_at=record.updated_at)
        version_ids = [version["version_id"] for version in versions]
        with st.container(border=True):
            st.subheader(record.title)
            st.caption(f"Last saved {record.updated_at[:16].replace('T', ' ')} UTC")
            if versions:
                st.caption(
                    f"{len(versions)} saved itinerary "
                    f"{'version' if len(versions) == 1 else 'versions'}"
                )
                version_ids = [version["version_id"] for version in versions]
                selected_version_id = st.selectbox(
                    "Itinerary to open",
                    version_ids,
                    index=len(version_ids) - 1,
                    format_func=lambda version_id: _version_label(
                        next(
                            version
                            for version in versions
                            if version["version_id"] == version_id
                        ),
                        version_ids.index(version_id),
                    ),
                    key=f"saved-version-{record.trip_id}",
                    label_visibility="collapsed",
                )
                st.button(
                    "Open itinerary",
                    icon=":material/folder_open:",
                    key=f"open-{record.trip_id}",
                    on_click=_open_saved_itinerary,
                    args=(record, selected_version_id),
                )
                _render_itinerary_comparison(record, versions)
            else:
                st.button(
                    "Edit trip",
                    icon=":material/edit:",
                    key=f"open-{record.trip_id}",
                    on_click=_open_trip_for_edit,
                    args=(record,),
                )
        if open_itinerary.get("trip_id") == record.trip_id:
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
