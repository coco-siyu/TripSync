"""TripSync Streamlit application entry point."""

import streamlit as st

from src.catalog_ui import render_catalog_workspace
from src.feedback_ui import render_feedback_insights
from src.trips_ui import render_saved_trips
from src.ui import _apply_styles, render_app


st.set_page_config(
    page_title="TripSync · Plan together",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Shared layout and navigation styles must load before every workspace, not
# only after the plan page starts rendering.
_apply_styles()

if "app_workspace" not in st.session_state:
    st.session_state.app_workspace = "Plan a trip"

workspaces = ["Plan a trip", "My trips", "Feedback insights", "Curate catalog"]
with st.container(key="workspace-nav"):
    with st.container(horizontal=True):
        for workspace in workspaces:
            if st.button(
                workspace,
                key=f"workspace-{workspace.casefold().replace(' ', '-')}",
                type="primary" if st.session_state.app_workspace == workspace else "secondary",
            ):
                st.session_state.app_workspace = workspace
                st.rerun()

view = st.session_state.app_workspace
if view == "Curate catalog":
    render_catalog_workspace()
elif view == "My trips":
    render_saved_trips()
elif view == "Feedback insights":
    render_feedback_insights()
else:
    render_app()
