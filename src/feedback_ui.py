"""Streamlit workspace for reviewing anonymous TripSync feedback."""

from __future__ import annotations

import json
import hmac
import os

import pandas as pd
import streamlit as st

from src.feedback import list_feedback, list_overall_experience_feedback
from src.feedback_insights import (
    build_feedback_insights,
    feedback_dashboard_data,
    feedback_insights_as_dict,
)


def _rating_label(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f} / 5"


@st.cache_data(ttl="5m", show_spinner=False)
def _load_dashboard_records() -> tuple[list, list]:
    """Load the small admin dataset once per cache window."""

    return list_feedback(), list_overall_experience_feedback()


def _dashboard_chart_data(feedback: list, overall: list) -> dict[str, pd.DataFrame]:
    """Return privacy-safe DataFrames for the five monitoring charts."""

    data = feedback_dashboard_data(feedback, overall)
    return {
        name: pd.DataFrame(rows)
        for name, rows in data.items()
    }


def render_feedback_insights() -> None:
    """Render protected, privacy-safe feedback metrics and export."""

    expected_password = os.getenv("TRIPSYNC_ADMIN_PASSWORD", "")
    if expected_password and not st.session_state.get("feedback_insights_authorized"):
        st.title("Feedback insights")
        with st.form("feedback-insights-access"):
            password = st.text_input("Admin password", type="password")
            unlocked = st.form_submit_button("Open insights", icon=":material/lock_open:")
        if unlocked:
            if hmac.compare_digest(password, expected_password):
                st.session_state.feedback_insights_authorized = True
                st.rerun()
            st.error("That password was not recognized.")
        return

    st.markdown('<div class="ts-section-label">Learn from real planning sessions</div>', unsafe_allow_html=True)
    st.title("Feedback insights")
    st.caption(
        "Shared feedback is stored securely. Exports omit session and itinerary identifiers."
    )
    refresh = st.button("Refresh dashboard", icon=":material/refresh:")
    if refresh:
        _load_dashboard_records.clear()
    feedback, overall = _load_dashboard_records()
    insights = build_feedback_insights(feedback, overall)

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

    chart_data = _dashboard_chart_data(feedback, overall)
    st.subheader("Feedback dashboard")
    st.caption("Five aggregated views of the feedback received so far. Refresh after collecting a new response.")

    left, right = st.columns(2)
    with left:
        with st.container(border=True):
            st.markdown("**Feedback received over time**")
            daily = chart_data["daily_submissions"]
            st.line_chart(
                daily,
                x="Date",
                y="Submissions",
                color="Feedback type",
                height=260,
            )
    with right:
        with st.container(border=True):
            st.markdown("**Helpful rate by generated experience**")
            helpful_rates = chart_data["helpful_rates"]
            if helpful_rates.empty:
                st.caption("Rate a story or suggestion to populate this chart.")
            else:
                st.bar_chart(
                    helpful_rates,
                    x="Experience",
                    y="Helpful rate",
                    color="#5BA7D6",
                    height=260,
                )

    left, right = st.columns(2)
    with left:
        with st.container(border=True):
            st.markdown("**Helpful and not-useful responses**")
            thumbs = chart_data["thumb_counts"]
            if thumbs.empty:
                st.caption("No generated-experience ratings yet.")
            else:
                st.bar_chart(
                    thumbs,
                    x="Experience",
                    y="Responses",
                    color="Rating",
                    stack=False,
                    height=260,
                )
    with right:
        with st.container(border=True):
            st.markdown("**Average overall plan rating**")
            averages = chart_data["rating_averages"]
            if averages.empty:
                st.caption("Rate a complete plan to populate this chart.")
            else:
                st.bar_chart(
                    averages,
                    x="Rating",
                    y="Average score",
                    color="#5BA7D6",
                    height=260,
                )

    with st.container(border=True):
        st.markdown("**Overall plan rating distribution**")
        distribution = chart_data["score_distribution"]
        if not overall:
            st.caption("Rate a complete plan to populate this chart.")
        else:
            st.bar_chart(
                distribution,
                x="Score",
                y="Responses",
                color="Rating",
                stack=False,
                height=280,
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
