"""TripSync Streamlit application entry point."""

import streamlit as st


st.set_page_config(page_title="TripSync", page_icon="🧭", layout="wide")

st.title("TripSync")
st.caption(
    "An AI-powered group travel planner that balances traveler preferences, "
    "constraints, and interests."
)

st.info(
    "TripSync is currently in development. The first interactive traveler form "
    "will be added in the next milestone."
)
