"""TripSync Streamlit application entry point."""

import streamlit as st

from src.auth_ui import initialize_account_state, render_account_workspace
from src.catalog_ui import render_catalog_workspace
from src.feedback_ui import render_feedback_insights
from src.trips_ui import render_saved_trips
from src.ui import _apply_styles, _initialize_state, _start_new_trip, render_app


st.set_page_config(
    page_title="TripSync · Plan together",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="collapsed",
)

if "app_workspace" not in st.session_state:
    st.session_state.app_workspace = "Plan a trip"

# Every workspace needs the shared browser-session state, even when the user
# opens a secondary page before visiting the planner.
_initialize_state()
initialize_account_state()

# Shared layout and workspace-specific colors must load before every page.
_apply_styles(st.session_state.app_workspace)

workspaces = [
    "Plan a trip",
    "My trips",
    "Account",
    "Feedback insights",
    "Curate catalog",
]
with st.container(key="workspace-nav"):
    brand_col, links_col = st.columns([1.2, 4], vertical_alignment="center")
    brand_col.markdown(
        '<div class="ts-nav-brand">trip<span>sync</span></div>',
        unsafe_allow_html=True,
    )
    with links_col.container(
        horizontal=True,
        horizontal_alignment="right",
        vertical_alignment="center",
        gap="small",
    ):
        for workspace in workspaces:
            if st.button(
                workspace,
                key=f"workspace-{workspace.casefold().replace(' ', '-')}",
                type=(
                    "primary"
                    if st.session_state.app_workspace == workspace
                    else "tertiary"
                ),
            ):
                if workspace == "Plan a trip":
                    _start_new_trip()
                else:
                    st.session_state.app_workspace = workspace
                st.rerun()

view = st.session_state.app_workspace
with st.container(key="workspace-page"):
    if view == "Curate catalog":
        render_catalog_workspace()
    elif view == "My trips":
        render_saved_trips()
    elif view == "Account":
        render_account_workspace()
    elif view == "Feedback insights":
        render_feedback_insights()
    else:
        render_app()
