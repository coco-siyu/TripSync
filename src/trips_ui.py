"""Saved-trip browser for the Streamlit workspace."""

from __future__ import annotations

import streamlit as st

from src.trips import SavedTrip, list_saved_trips


def _open_trip(record: SavedTrip) -> None:
    st.session_state.trip_request = record.trip.model_dump(mode="json")
    st.session_state.trip_basics = record.trip.model_dump(mode="json", exclude={"travelers"})
    for key, value in record.state.items():
        st.session_state[key] = value
    st.session_state.saved_trip_id = record.trip_id
    st.session_state.planner_step = "results"
    st.session_state.app_workspace = "Plan a trip"


def render_saved_trips() -> None:
    st.markdown('<div class="ts-section-label">Your saved plans</div>', unsafe_allow_html=True)
    st.title("Pick up where you left off")
    st.caption(
        "This demo shows plans saved in this browser session. Other visitors' "
        "plans stay private."
    )
    records = list_saved_trips(st.session_state.feedback_session_id)
    if not records:
        st.info("Save a plan from the results screen and it will appear here.", icon=":material/bookmark:")
        return
    for record in records:
        with st.container(border=True):
            st.subheader(record.title)
            st.caption(f"Last saved {record.updated_at[:16].replace('T', ' ')} UTC")
            st.button("Open plan", icon=":material/folder_open:", key=f"open-{record.trip_id}", on_click=_open_trip, args=(record,))
