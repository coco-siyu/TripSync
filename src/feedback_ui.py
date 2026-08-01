"""Streamlit workspace for reviewing anonymous TripSync feedback."""

from __future__ import annotations

import json

import streamlit as st

from src.feedback_insights import feedback_insights_as_dict, load_feedback_insights


def _rating_label(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f} / 5"


def render_feedback_insights() -> None:
    """Render local feedback metrics, comments, and a privacy-safe export."""

    st.markdown('<div class="ts-section-label">Learn from real planning sessions</div>', unsafe_allow_html=True)
    st.title("Feedback insights")
    st.caption(
        "Only feedback saved on this computer is shown. Exports omit session and itinerary identifiers."
    )
    insights = load_feedback_insights()

    with st.container(horizontal=True):
        st.metric("Overall plan ratings", insights.overall_response_count, border=True)
        st.metric("Story + suggestion ratings", insights.generated_feedback_count, border=True)
        st.metric("Written comments", len(insights.comments), border=True)

    if not insights.overall_response_count and not insights.generated_feedback_count:
        st.info(
            "No feedback has been saved yet. Rate an itinerary, trip story, or adjustment suggestion to start learning from it.",
            icon=":material/insights:",
        )
        return

    st.subheader("Overall experience")
    with st.container(horizontal=True):
        st.metric("Helpful for planning", _rating_label(insights.helpfulness_average), border=True)
        st.metric("Clear and easy to use", _rating_label(insights.clarity_average), border=True)
        st.metric("Fits the group", _rating_label(insights.group_fit_average), border=True)

    st.subheader("Thumb feedback")
    st.caption("A compact count for each generated experience.")
    with st.container(horizontal=True):
        st.metric(
            "Trip stories",
            f"{insights.story_up} helpful · {insights.story_down} not useful",
            border=True,
        )
        st.metric(
            "Adjustment suggestions",
            f"{insights.adjustment_up} helpful · {insights.adjustment_down} not useful",
            border=True,
        )

    with st.container(horizontal=True):
        st.download_button(
            "Download feedback JSON",
            data=json.dumps(feedback_insights_as_dict(insights), indent=2),
            file_name="tripsync-feedback-insights.json",
            mime="application/json",
            icon=":material/download:",
            on_click="ignore",
        )

    st.subheader("Recent written feedback")
    if not insights.comments:
        st.caption("No optional written feedback yet.")
        return
    for comment in insights.comments:
        with st.container(border=True):
            st.caption(
                f"{comment.source} · {comment.rating} · {comment.created_at[:16].replace('T', ' ')} UTC"
            )
            st.write(comment.comment)
