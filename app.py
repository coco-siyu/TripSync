"""TripSync Streamlit application entry point."""

import streamlit as st

from src.catalog_ui import render_catalog_workspace
from src.feedback_ui import render_feedback_insights
from src.trips_ui import render_saved_trips
from src.ui import render_app


st.set_page_config(
    page_title="TripSync · Plan together",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="collapsed",
)

if "app_workspace" not in st.session_state:
    st.session_state.app_workspace = "Plan a trip"

view = st.segmented_control(
    "TripSync workspace",
    ["Plan a trip", "My trips", "Feedback insights", "Curate catalog"],
    label_visibility="collapsed",
    key="app_workspace",
)
if view == "Curate catalog":
    render_catalog_workspace()
elif view == "My trips":
    render_saved_trips()
elif view == "Feedback insights":
    render_feedback_insights()
else:
    render_app()
