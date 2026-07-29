"""TripSync Streamlit application entry point."""

import streamlit as st

from src.ui import render_app


st.set_page_config(
    page_title="TripSync · Plan together",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="collapsed",
)

render_app()
