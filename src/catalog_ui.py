"""The in-app workspace for reviewing and promoting activity candidates."""

from __future__ import annotations

import hmac
import os
from pathlib import Path

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from src.catalog import (
    auto_curate_candidates,
    build_activity,
    candidate_files,
    delete_candidates_from_batch,
    delete_activities,
    load_candidate_batch,
    load_curated_activities,
    save_activities,
    save_activity,
    update_activity,
)
from src.catalog_import import CatalogImportError, fetch_candidates, resolve_city, write_candidate_file
from src.draft_curation import draft_activity
from src.models import Activity, BudgetLevel, WalkingLevel
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


def _catalog_access_allowed() -> bool:
    """Keep shared catalog edits available only to the configured administrator."""

    expected_password = os.getenv("TRIPSYNC_ADMIN_PASSWORD", "")
    if not expected_password:
        st.title("Curate catalog")
        st.error(
            "This admin workspace is disabled until TRIPSYNC_ADMIN_PASSWORD is configured.",
            icon=":material/lock:",
        )
        return False
    if st.session_state.get("catalog_authorized"):
        return True
    st.title("Curate catalog")
    with st.form("catalog-access"):
        password = st.text_input("Admin password", type="password")
        unlocked = st.form_submit_button("Open catalog", icon=":material/lock_open:")
    if unlocked:
        if hmac.compare_digest(password, expected_password):
            st.session_state.catalog_authorized = True
            st.rerun()
        st.error("That password was not recognized.")
    return False


def _selected_activity_ids(activities: list, selected_rows: list[int]) -> list[str]:
    """Return valid selected IDs, ignoring stale dataframe row positions."""

    return [
        activities[index].id
        for index in selected_rows
        if isinstance(index, int) and 0 <= index < len(activities)
    ]


def _selected_candidate_ids(rows: list[dict], selected_rows: list[int]) -> list[str]:
    """Return stable Wikidata IDs for valid selections in one candidate batch."""

    return [
        rows[index]["candidate"]["wikidata_id"]
        for index in selected_rows
        if isinstance(index, int) and 0 <= index < len(rows)
    ]


def _filter_published_catalog(country: str, city: str) -> None:
    """Preselect the published-catalog filters from the batch completion action."""

    st.session_state["catalog-country-filter"] = country
    st.session_state["catalog-city-filter"] = city


def _activity_categories(current: str) -> list[str]:
    options = [
        "museum", "historic_site", "landmark", "architecture", "park",
        "food_market", "university_campus", "outdoor",
    ]
    return options if current in options else [current, *options]


def _render_published_activity_editor(activity: Activity) -> None:
    """Edit a live activity without changing its stable activity identifier."""

    st.subheader(f"Edit {activity.name}")
    st.caption("The activity name, city, and country are kept fixed so links and saved plans remain stable.")
    categories = _activity_categories(activity.category)
    with st.form(f"published-activity-editor-{activity.id}", border=True):
        category = st.selectbox(
            "Primary category", categories, index=categories.index(activity.category)
        )
        category_tags = st.multiselect(
            "Additional category tags",
            ["cultural", "religious_site", "outdoor", "architecture", "food", "shopping"],
            default=activity.category_tags,
            accept_new_options=True,
        )
        interests = st.multiselect(
            "Interest tags", INTEREST_OPTIONS, default=activity.interests,
            accept_new_options=True,
        )
        left, right = st.columns(2)
        with left:
            walking = st.segmented_control(
                "Walking", list(WalkingLevel), default=activity.walking_level,
                format_func=lambda item: item.value.title(),
            )
            duration = st.number_input(
                "Typical visit (hours)", min_value=0.5, max_value=12.0,
                value=activity.duration_hours, step=0.5,
            )
            indoor = st.checkbox("Primarily indoors", value=activity.indoor)
        with right:
            budget = st.segmented_control(
                "Budget", list(BudgetLevel), default=activity.budget_level,
                format_func=lambda item: item.value.title(),
            )
            reservation = st.checkbox(
                "Advance reservation normally needed", value=activity.reservation_required
            )
            family = st.checkbox("Family-friendly", value=activity.family_friendly)
        accessibility = st.text_input("Accessibility notes", value=activity.accessibility_notes)
        description = st.text_area("Short factual description", value=activity.description)
        source_url = st.text_input("Verification URL", value=str(activity.source_url))
        save = st.form_submit_button("Validate and save changes", icon=":material/save:", type="primary")
    if save:
        try:
            updated = Activity.model_validate({
                **activity.model_dump(mode="json"),
                "category": category,
                "category_tags": category_tags,
                "interests": interests,
                "walking_level": walking,
                "budget_level": budget,
                "duration_hours": duration,
                "indoor": indoor,
                "family_friendly": family,
                "reservation_required": reservation,
                "accessibility_notes": accessibility,
                "description": description,
                "source_url": source_url,
            })
            update_activity(updated)
            st.success(f"Saved changes to {updated.name}.")
            st.rerun()
        except (ValidationError, ValueError) as error:
            st.error(str(error), icon=":material/error:")


def _render_published_catalog() -> None:
    """Let a curator inspect and deliberately remove published catalog records."""

    activities = load_curated_activities()
    st.subheader("Published activity catalog")
    st.caption("Filter by country and city. Select records only when you want to remove them.")
    if not activities:
        st.info("No published activities yet.", icon=":material/dataset:")
        return

    countries = sorted({activity.country for activity in activities}, key=str.casefold)
    country = st.selectbox("Country filter", ["All countries", *countries], key="catalog-country-filter")
    city_options = sorted(
        {
            activity.city
            for activity in activities
            if country == "All countries" or activity.country == country
        },
        key=str.casefold,
    )
    city = st.selectbox("City filter", ["All cities", *city_options], key="catalog-city-filter")
    filtered = [
        activity
        for activity in activities
        if (country == "All countries" or activity.country == country)
        and (city == "All cities" or activity.city == city)
    ]
    table = pd.DataFrame(
        [
            {
                "Name": activity.name,
                "City": activity.city,
                "Country": activity.country,
                "Category": activity.category.replace("_", " "),
                "Interests": ", ".join(activity.interests),
            }
            for activity in filtered
        ]
    )
    event = st.dataframe(
        table,
        hide_index=True,
        on_select="rerun",
        selection_mode="multi-row",
        key="published-activity-table",
    )
    selected_ids = _selected_activity_ids(filtered, event.selection.rows)
    st.subheader("Manage selection")
    edit_tab, delete_tab = st.tabs(
        ["Edit details", "Delete activities"],
        key="published-catalog-actions",
        on_change="rerun",
    )
    if edit_tab.open:
        with edit_tab:
            if len(selected_ids) == 1:
                selected_activity = next(
                    activity for activity in filtered if activity.id == selected_ids[0]
                )
                _render_published_activity_editor(selected_activity)
            elif len(selected_ids) > 1:
                st.info(
                    "You selected multiple activities. Select exactly one to edit its planning details.",
                    icon=":material/edit:",
                )
            else:
                st.info(
                    "Select one published activity above, then edit its tags and planning details here.",
                    icon=":material/touch_app:",
                )
    if delete_tab.open:
        with delete_tab:
            selection_signature = "-".join(sorted(selected_ids)) or "none"
            if selected_ids:
                st.warning(
                    f"{len(selected_ids)} selected record(s) will be permanently removed from the shared catalog."
                )
            else:
                st.caption("Select one or more activities above to enable deletion.")
            confirmed = st.checkbox(
                "I reviewed the selected activities and want to remove them permanently.",
                key=f"confirm-catalog-delete-{selection_signature}",
                disabled=not selected_ids,
            )
            if st.button(
                "Delete selected activities",
                icon=":material/delete:",
                type="primary",
                disabled=not selected_ids or not confirmed,
                key="published-catalog-delete",
            ):
                removed = delete_activities(selected_ids)
                st.success(f"Removed {removed} activity record(s) from the catalog.")
                st.rerun()


def _render_batch_curation() -> None:
    """Fetch, bulk-promote, refine, or discard one raw candidate batch."""

    st.subheader("Fetch and batch curate")
    st.caption("Search Wikidata candidates, bulk-promote safe drafts, or remove unwanted source records.")

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
    automatic_activities, automatically_skipped = auto_curate_candidates(
        [row["candidate"] for row in rows], batch["city"], batch["country"]
    )
    published_ids = {activity.id for activity in load_curated_activities()}
    eligible_to_add = [
        activity for activity in automatic_activities if activity.id not in published_ids
    ]
    batch_result_key = f"catalog-batch-result-{selected_file.name}"
    with st.container(border=True):
        st.markdown("**Fast batch curation**")
        st.caption(
            f"{len(automatic_activities)} candidates have safe, Pydantic-valid drafts. "
            f"{len(automatically_skipped)} are excluded automatically (for example hotels or transport)."
        )
        if len(eligible_to_add) != len(automatic_activities):
            st.caption(
                f"{len(eligible_to_add)} are new; "
                f"{len(automatic_activities) - len(eligible_to_add)} are already in the catalog."
            )
        previous_result = st.session_state.get(batch_result_key)
        if previous_result:
            st.success(previous_result, icon=":material/check_circle:")
        if eligible_to_add:
            if st.button(
                f"Add all {len(eligible_to_add)} eligible candidates",
                icon=":material/library_add:",
                type="primary",
                key=f"catalog-batch-add-{selected_file.name}",
                help="Adds every eligible candidate in this batch. Table selections below are only for detailed editing.",
            ):
                added, duplicates = save_activities(eligible_to_add)
                st.session_state[batch_result_key] = (
                    f"Added {len(added)} activities to the catalog. "
                    f"Skipped {len(duplicates)} existing duplicates."
                )
                st.rerun()
        else:
            st.success(
                f"All {len(automatic_activities)} eligible candidates are already published.",
                icon=":material/check_circle:",
            )
            st.button(
                f"View all {len(automatic_activities)} published activities",
                icon=":material/visibility:",
                type="primary",
                key=f"catalog-batch-view-{selected_file.name}",
                on_click=_filter_published_catalog,
                args=(batch["country"], batch["city"]),
            )
    display = pd.DataFrame([{key: value for key, value in row.items() if key not in {"source_url", "candidate"}} for row in rows])
    event = st.dataframe(
        display,
        hide_index=True,
        on_select="rerun",
        selection_mode="multi-row",
        key="candidate-table",
    )
    selected_rows = event.selection.rows
    selected_candidate_ids = _selected_candidate_ids(rows, selected_rows)
    candidate_signature = "-".join(sorted(selected_candidate_ids)) or "none"
    if selected_candidate_ids:
        st.warning(
            f"{len(selected_candidate_ids)} selected source candidate(s) will be removed from this batch only."
        )
    else:
        st.caption("Select source candidates above to enable batch removal.")
    remove_confirmed = st.checkbox(
        "I reviewed the selected source candidates and want to remove them from this batch.",
        key=f"confirm-batch-delete-{selected_file.name}-{candidate_signature}",
        disabled=not selected_candidate_ids,
    )
    if st.button(
        "Remove selected candidates from this batch",
        icon=":material/delete_sweep:",
        type="primary",
        disabled=not selected_candidate_ids or not remove_confirmed,
        key=f"batch-delete-{selected_file.name}",
    ):
        removed = delete_candidates_from_batch(selected_file, selected_candidate_ids)
        st.success(f"Removed {removed} source candidate(s) from {selected_file.name}.")
        st.rerun()
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


def render_catalog_workspace() -> None:
    """Render the protected, two-part catalog management workspace."""

    if not _catalog_access_allowed():
        return
    st.markdown('<div class="ts-section-label">Build the catalog</div>', unsafe_allow_html=True)
    st.title("Curate places your travelers will love")
    st.caption("Manage the live catalog separately from fetching and reviewing source candidates.")
    published_tab, batch_tab = st.tabs(
        ["Published activities", "Fetch & batch curate"],
        key="catalog-workspace-tabs",
        on_change="rerun",
    )
    if published_tab.open:
        with published_tab:
            _render_published_catalog()
    if batch_tab.open:
        with batch_tab:
            _render_batch_curation()
