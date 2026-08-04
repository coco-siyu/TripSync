"""The in-app workspace for reviewing and promoting activity candidates."""

from __future__ import annotations

import hmac
import os
from pathlib import Path

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from src.catalog import (
    activity_id,
    auto_curate_candidates,
    candidate_files,
    delete_candidates_from_batch,
    delete_activities,
    load_candidate_batch,
    load_curated_activities,
    save_activities,
    update_activity,
)
from src.catalog_import import CatalogImportError, fetch_candidates, resolve_city, write_candidate_file
from src.models import Activity, BudgetLevel, WalkingLevel
from src.ui import INTEREST_OPTIONS


def _candidate_rows(batch: dict) -> list[dict]:
    return [
        {"Name": item["name"], "Types": ", ".join(item.get("wikidata_types", [])),
         "Review note": " ".join(item.get("review_flags", [])) or "Ready to review",
         "source_url": item["source_url"], "candidate": item}
        for item in batch["candidates"]
    ]


def _candidate_batch_label(path: Path) -> str:
    """Describe a review batch with travel context instead of a JSON filename."""

    batch = load_candidate_batch(path)
    return f"{batch['city']}, {batch['country']} · {len(batch['candidates'])} retrieved places"


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
    """Return stable Wikidata IDs for selected raw candidates."""

    return [
        rows[index]["candidate"]["wikidata_id"]
        for index in selected_rows
        if isinstance(index, int) and 0 <= index < len(rows)
    ]


def _filter_published_catalog(country: str, city: str) -> None:
    """Preselect the published-catalog filters from the batch completion action."""

    st.session_state["catalog-country-filter"] = country
    st.session_state["catalog-city-filter"] = city


def _show_catalog_write_error(error: Exception) -> None:
    """Turn a missing shared-catalog migration into an actionable UI message."""

    if "catalog_activities" in str(error):
        st.error(
            "The shared catalog table has not been created in Supabase yet. "
            "Run `supabase/schema.sql` in the Supabase SQL Editor, then try again.",
            icon=":material/database_off:",
        )
        return
    st.error(
        "TripSync could not update the shared catalog. Check the Supabase connection and try again.",
        icon=":material/cloud_off:",
    )


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
        except Exception as error:  # noqa: BLE001 - remote persistence must not crash the UI
            _show_catalog_write_error(error)


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
                try:
                    removed = delete_activities(selected_ids)
                    st.success(f"Removed {removed} activity record(s) from the catalog.")
                    st.rerun()
                except Exception as error:  # noqa: BLE001 - remote persistence must not crash the UI
                    _show_catalog_write_error(error)


def _render_catalog_import() -> None:
    """Fetch a batch, then select raw candidates to publish or discard."""

    st.subheader("Fetch and batch curate")
    st.caption(
        "Fetch a city or choose a review batch. Select places in the table, then add the "
        "eligible ones to the published catalog or remove unsuitable source candidates."
    )

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
    selected_file = st.selectbox("Candidate batch", files, format_func=_candidate_batch_label)
    batch = load_candidate_batch(selected_file)
    rows = _candidate_rows(batch)
    automatic_activities, automatically_skipped = auto_curate_candidates(
        [row["candidate"] for row in rows], batch["city"], batch["country"]
    )
    activity_by_id = {activity.id: activity for activity in automatic_activities}
    published_ids = {activity.id for activity in load_curated_activities()}
    batch_result_key = f"catalog-batch-result-{selected_file.name}"
    st.subheader("Retrieved places")
    st.caption("Use the checkbox in the table header to select every place in this batch.")
    display = pd.DataFrame(
        [
            {
                "Name": row["Name"],
                "City": batch["city"],
                "Country": batch["country"],
                "Category": activity_by_id.get(
                    activity_id(batch["city"], row["Name"]), None
                ).category.replace("_", " ")
                if activity_id(batch["city"], row["Name"]) in activity_by_id
                else "Not eligible",
                "Interests": ", ".join(
                    activity_by_id[activity_id(batch["city"], row["Name"])].interests
                )
                if activity_id(batch["city"], row["Name"]) in activity_by_id
                else "",
                "Review note": row["Review note"],
            }
            for row in rows
        ]
    )
    event = st.dataframe(
        display,
        hide_index=True,
        on_select="rerun",
        selection_mode="multi-row",
        key=f"candidate-table-{selected_file.name}",
    )
    selected_candidate_ids = _selected_candidate_ids(rows, event.selection.rows)
    selected_activities = [
        activity_by_id[activity_id(batch["city"], row["Name"])]
        for row in rows
        if row["candidate"].get("wikidata_id") in selected_candidate_ids
        and activity_id(batch["city"], row["Name"]) in activity_by_id
    ]
    selected_eligible = [
        activity for activity in selected_activities if activity.id not in published_ids
    ]
    selection_signature = "-".join(sorted(selected_candidate_ids)) or "none"

    with st.container(border=True):
        st.markdown("**Selected places**")
        st.caption(
            f"{len(automatic_activities)} candidates have safe, Pydantic-valid drafts. "
            f"{len(automatically_skipped)} are excluded automatically (for example hotels or transport)."
        )
        if selected_candidate_ids:
            st.caption(f"{len(selected_candidate_ids)} retrieved place(s) selected.")
        else:
            st.caption("Select one or more retrieved places above to enable an action.")
        previous_result = st.session_state.get(batch_result_key)
        if previous_result:
            st.success(previous_result, icon=":material/check_circle:")
        add_tab, remove_tab = st.tabs(
            ["Add to published", "Remove from batch"],
            key=f"batch-actions-{selected_file.name}",
            on_change="rerun",
        )
        if add_tab.open:
            with add_tab:
                st.caption(
                    "Select every eligible row in the table to add the entire eligible batch. "
                    "Existing published activities are skipped."
                )
                if st.button(
                    f"Add {len(selected_eligible)} selected eligible candidate(s)",
                    icon=":material/library_add:",
                    type="primary",
                    disabled=not selected_eligible,
                    key=f"catalog-batch-add-{selected_file.name}",
                ):
                    try:
                        added, duplicates = save_activities(selected_eligible)
                        st.session_state[batch_result_key] = (
                            f"Added {len(added)} activities to the catalog. "
                            f"Skipped {len(duplicates)} existing duplicates."
                        )
                        _filter_published_catalog(batch["country"], batch["city"])
                        st.rerun()
                    except Exception as error:  # noqa: BLE001 - remote persistence must not crash the UI
                        _show_catalog_write_error(error)
        if remove_tab.open:
            with remove_tab:
                st.caption("Removal changes only this raw retrieval batch, not published activities.")
                confirmed = st.checkbox(
                    "I want to remove the selected retrieved places.",
                    key=f"confirm-batch-delete-{selected_file.name}-{selection_signature}",
                    disabled=not selected_candidate_ids,
                )
                if st.button(
                    "Remove selected retrieved places",
                    icon=":material/delete_sweep:",
                    type="primary",
                    disabled=not selected_candidate_ids or not confirmed,
                    key=f"batch-delete-{selected_file.name}",
                ):
                    removed = delete_candidates_from_batch(selected_file, selected_candidate_ids)
                    st.success(f"Removed {removed} retrieved place(s) from this batch.")
                    st.rerun()


def render_catalog_workspace() -> None:
    """Render separate views for live activities and fetched source candidates."""

    if not _catalog_access_allowed():
        return
    st.markdown('<div class="ts-section-label">Build the catalog</div>', unsafe_allow_html=True)
    st.title("Curate places your travelers will love")
    st.caption("Manage published activities separately from fetching and reviewing source candidates.")
    published_tab, fetch_tab = st.tabs(
        ["Published activities", "Fetch & batch curate"],
        key="catalog-workspace-tabs",
        on_change="rerun",
    )
    if published_tab.open:
        with published_tab:
            _render_published_catalog()
    if fetch_tab.open:
        with fetch_tab:
            _render_catalog_import()
