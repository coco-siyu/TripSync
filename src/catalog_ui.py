"""The in-app workspace for reviewing and promoting activity candidates."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from src.catalog import build_activity, candidate_files, load_candidate_batch, save_activity
from src.catalog_import import CatalogImportError, fetch_candidates, resolve_city, write_candidate_file
from src.draft_curation import draft_activity
from src.models import BudgetLevel, WalkingLevel
from src.ui import INTEREST_OPTIONS


def _candidate_rows(batch: dict) -> list[dict]:
    return [
        {"Name": item["name"], "Types": ", ".join(item.get("wikidata_types", [])),
         "Review note": " ".join(item.get("review_flags", [])) or "Ready to review",
         "source_url": item["source_url"], "candidate": item}
        for item in batch["candidates"]
    ]


def _selected_candidate(rows: list[dict], selected_rows: list[int]) -> dict | None:
    """Return the selected candidate only when the table selection is current.

    Streamlit preserves a dataframe selection across reruns. When the reviewer
    changes batches, that old row index may not exist in the newly selected
    batch, so it must be treated as no selection rather than indexed directly.
    """

    if len(selected_rows) != 1:
        return None
    selected_index = selected_rows[0]
    if not isinstance(selected_index, int) or not 0 <= selected_index < len(rows):
        return None
    return rows[selected_index]["candidate"]


def render_catalog_workspace() -> None:
    """Render the candidate search, selection, and Pydantic-backed editor."""

    st.markdown('<div class="ts-section-label">Build the catalog</div>', unsafe_allow_html=True)
    st.title("Curate places your travelers will love")
    st.caption("Search Wikidata candidates, then promote only reviewed places into TripSync.")

    with st.container(border=True, key="catalog-query"):
        with st.form("catalog-query-form"):
            city_name = st.text_input("City", value="Venice")
            country_name = st.text_input("Country", value="Italy")
            limit = st.slider("Candidates to fetch", 10, 50, 25)
            fetch = st.form_submit_button("Fetch place candidates", icon=":material/travel_explore:", type="primary")
        if fetch:
            try:
                with st.spinner(f"Finding places in {city_name}…"):
                    city = resolve_city(city_name, country_name)
                    candidates = fetch_candidates(city, limit=limit)
                    path = write_candidate_file(city, candidates, Path("data/candidates"))
                st.success(f"Saved {len(candidates)} candidates to {path.name}.")
            except CatalogImportError as error:
                st.error(str(error), icon=":material/cloud_off:")

    files = candidate_files()
    if not files:
        st.info("Fetch a city above to start a review batch.", icon=":material/search:")
        return
    selected_file = st.selectbox("Candidate batch", files, format_func=lambda file: file.name)
    batch = load_candidate_batch(selected_file)
    rows = _candidate_rows(batch)
    display = pd.DataFrame([{key: value for key, value in row.items() if key not in {"source_url", "candidate"}} for row in rows])
    event = st.dataframe(display, hide_index=True, on_select="rerun", selection_mode="single-row", key="candidate-table")
    selected_rows = event.selection.rows
    candidate = _selected_candidate(rows, selected_rows)
    if candidate is None:
        st.caption("Select one place to complete its planning details.")
        return
    draft = draft_activity(candidate)
    st.subheader(f"Review {candidate['name']}")
    if candidate.get("review_flags"):
        st.warning(" ".join(candidate["review_flags"]), icon=":material/flag:")
    with st.form(f"activity-editor-{candidate['wikidata_id']}", border=True):
        name = st.text_input("Activity name", value=candidate["name"])
        category = st.selectbox("Primary category", ["museum", "historic_site", "landmark", "architecture", "park", "food_market", "university_campus", "outdoor"], index=["museum", "historic_site", "landmark", "architecture", "park", "food_market", "university_campus", "outdoor"].index(draft.category))
        category_tags = st.multiselect("Additional category tags", ["cultural", "religious_site", "outdoor", "architecture", "food", "shopping"], default=list(draft.category_tags), accept_new_options=True)
        interests = st.multiselect("Interest tags", INTEREST_OPTIONS, default=list(draft.interests), accept_new_options=True)
        left, right = st.columns(2)
        with left:
            walking = st.segmented_control("Walking", list(WalkingLevel), default=WalkingLevel(draft.walking_level), format_func=lambda item: item.value.title())
            duration = st.number_input("Typical visit (hours)", min_value=0.5, max_value=12.0, value=draft.duration_hours, step=0.5)
            indoor = st.checkbox("Primarily indoors", value=draft.indoor)
        with right:
            budget = st.segmented_control("Budget", list(BudgetLevel), default=BudgetLevel(draft.budget_level), format_func=lambda item: item.value.title())
            reservation = st.checkbox("Advance reservation normally needed", value=draft.reservation_required)
            family = st.checkbox("Family-friendly", value=True)
        accessibility = st.text_input("Accessibility notes", value="Check the official visitor information before visiting.")
        description = st.text_area("Short factual description", value=f"A visitor experience in {batch['city']}.")
        source_url = st.text_input("Verification URL", value=candidate["source_url"])
        save = st.form_submit_button("Validate and add to catalog", icon=":material/add_circle:", type="primary")
    if save:
        try:
            activity = build_activity(candidate, batch["city"], batch["country"], {
                "name": name, "category": category, "category_tags": category_tags, "interests": interests,
                "walking_level": walking, "budget_level": budget, "duration_hours": duration,
                "indoor": indoor, "family_friendly": family, "reservation_required": reservation,
                "accessibility_notes": accessibility, "description": description, "source_url": source_url,
            })
            save_activity(activity)
            st.success(f"{activity.name} is now a validated TripSync activity.")
        except (ValidationError, ValueError) as error:
            st.error(str(error), icon=":material/error:")
