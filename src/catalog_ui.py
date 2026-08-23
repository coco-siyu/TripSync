"""The in-app workspace for reviewing and promoting activity candidates."""

from __future__ import annotations

import hmac
import os

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from src.catalog import (
    auto_curate_candidates,
    delete_activities,
    delete_review_candidates,
    load_curated_activities,
    load_review_candidates,
    review_curate_candidates,
    save_activities,
    save_review_candidates,
    update_activity,
    update_activities,
)
from src.catalog_backfill import backfill_official_sites
from src.catalog_quality import (
    catalog_quality_rows,
    destination_quality_rows,
    summarize_catalog_quality,
)
from src.catalog_import import CatalogImportError, fetch_candidates, resolve_city
from src.models import Activity, BudgetLevel, WalkingLevel
from src.official_sites import enrich_candidate_official_sites
from src.ui import INTEREST_OPTIONS


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


def _filter_published_catalog(country: str, city: str) -> None:
    """Preselect the published-catalog filters from the batch completion action."""

    st.session_state["catalog-country-filter"] = country
    st.session_state["catalog-city-filter"] = city


def _show_catalog_write_error(error: Exception) -> None:
    """Turn a missing shared-catalog migration into an actionable UI message."""

    if "catalog_activities" in str(error) or "catalog_review_candidates" in str(error):
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
        official_url = st.text_input(
            "Possible official website",
            value=str(activity.official_url or ""),
            help=(
                "TripSync verifies changed URLs automatically before showing them "
                "to travelers as official sources."
            ),
        )
        st.text_input(
            "Official visit-planning URL",
            value=str(activity.official_visit_url or ""),
            disabled=True,
            help="Discovered automatically from the verified official website.",
        )
        st.text_input(
            "Official opening-hours URL",
            value=str(activity.official_hours_url or ""),
            disabled=True,
            help="Discovered automatically from the verified official website.",
        )
        st.text_input(
            "Official tickets URL",
            value=str(activity.official_tickets_url or ""),
            disabled=True,
            help="Discovered automatically from the verified official website.",
        )
        address = st.text_input("Address", value=activity.address or "")
        opening_hours = st.text_input(
            "Opening hours",
            value=activity.opening_hours or "",
            help="Community-maintained or curator-entered information. Keep the official link for current hours.",
        )
        osm_url = st.text_input("OpenStreetMap reference URL", value=str(activity.osm_url or ""))
        save = st.form_submit_button("Validate and save changes", icon=":material/save:", type="primary")
    if save:
        try:
            normalized_official_url = official_url.strip().rstrip("/")
            existing_official_url = str(activity.official_url or "").rstrip("/")
            keep_verified_site = bool(
                activity.official_site_verified
                and normalized_official_url
                and normalized_official_url == existing_official_url
            )
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
                "official_url": official_url or None,
                "official_site_verified": keep_verified_site,
                "official_visit_url": (
                    activity.official_visit_url if keep_verified_site else None
                ),
                "official_hours_url": (
                    activity.official_hours_url if keep_verified_site else None
                ),
                "official_tickets_url": (
                    activity.official_tickets_url if keep_verified_site else None
                ),
                "official_site_checked_at": (
                    activity.official_site_checked_at if keep_verified_site else None
                ),
                "address": address or None,
                "opening_hours": opening_hours or None,
                "osm_url": osm_url or None,
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


def _render_catalog_quality() -> None:
    """Show official-source coverage and run one automatic retry for all gaps."""

    completed_refresh = st.session_state.pop(
        "catalog-quality-refresh-result", None
    )
    if completed_refresh:
        st.toast(
            completed_refresh,
            icon=":material/verified:",
            duration=4,
        )
    activities = load_curated_activities()
    st.subheader("Official source coverage")
    st.caption(
        "TripSync saves a website only after opening the real organization domain. "
        "Wikidata may provide a locator URL, but visitor links come from the verified site itself."
    )
    if not activities:
        st.info("No published activities yet.", icon=":material/dataset:")
        return

    summary = summarize_catalog_quality(activities)
    metric_columns = st.columns(4)
    metric_columns[0].metric(
        "Verified official sites",
        f"{summary.verified_official_sites}/{summary.total}",
    )
    metric_columns[1].metric(
        "Official hours links",
        f"{summary.official_hours_links}/{summary.total}",
    )
    metric_columns[2].metric(
        "Official ticket links",
        f"{summary.official_ticket_links}/{summary.total}",
    )
    metric_columns[3].metric(
        "Mapped coordinates",
        f"{summary.coordinates}/{summary.total}",
    )

    st.markdown("#### Coverage by destination")
    st.dataframe(
        pd.DataFrame(destination_quality_rows(activities)),
        hide_index=True,
        column_config={
            column: st.column_config.ProgressColumn(
                column, min_value=0.0, max_value=1.0, format="percent"
            )
            for column in (
                "Official sites", "Hours links", "Ticket links", "Addresses",
                "Coordinates",
            )
        },
        key="catalog-quality-destinations",
    )

    quality_filter = st.selectbox(
        "Show activities",
        [
            "Missing verified official site",
            "Missing official hours link",
            "Missing official ticket link",
            "All activities",
        ],
        key="catalog-quality-filter",
    )
    if quality_filter == "Missing verified official site":
        filtered = [
            activity for activity in activities
            if not activity.official_site_verified
        ]
    elif quality_filter == "Missing official hours link":
        filtered = [
            activity for activity in activities
            if activity.official_hours_url is None
        ]
    elif quality_filter == "Missing official ticket link":
        filtered = [
            activity for activity in activities
            if activity.official_tickets_url is None
        ]
    else:
        filtered = activities
    st.dataframe(
        pd.DataFrame(catalog_quality_rows(filtered)),
        hide_index=True,
        column_config={
            "Official website": st.column_config.LinkColumn("Official website"),
            "Hours page": st.column_config.LinkColumn("Hours page"),
            "Tickets page": st.column_config.LinkColumn("Tickets page"),
        },
        key=f"catalog-quality-activities-{quality_filter}",
    )

    with st.container(border=True):
        st.markdown("#### Automatic source refresh")
        st.caption(
            "Retry every missing source in one batch. Verified links are saved directly; "
            "unreachable or mismatched sites remain missing and can retry next week."
        )
        if st.button(
            "Refresh missing official sources",
            icon=":material/sync:",
            type="primary",
            key="refresh-official-sites",
        ):
            try:
                with st.status(
                    "Checking actual official websites…", expanded=True
                ) as status:
                    updated, refreshed, unresolved = backfill_official_sites(
                        activities
                    )
                    update_activities(
                        updated,
                        sync_embeddings=False,
                    )
                    status.update(
                        label=(
                            f"Saved {refreshed} verified official source(s); "
                            f"{unresolved} remain unavailable."
                        ),
                        state="complete",
                        expanded=False,
                    )
                st.session_state["catalog-quality-refresh-result"] = (
                    f"Saved {refreshed} verified official source(s); "
                    f"{unresolved} remain unavailable."
                )
                st.rerun()
            except CatalogImportError as error:
                st.error(str(error), icon=":material/cloud_off:")
            except Exception as error:  # noqa: BLE001 - remote persistence must remain actionable
                _show_catalog_write_error(error)


def _render_catalog_import() -> None:
    """Fetch one city and publish quality-approved attractions directly."""

    st.subheader("Fetch and batch curate")
    st.caption(
        "Fetch a city once. Clear visitor attractions are validated and published immediately; "
        "hotels, transport, organisations, and event records are filtered out before they reach the catalog."
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
                    raw_candidates = [candidate.__dict__ for candidate in candidates]
                    enriched_candidates = enrich_candidate_official_sites(
                        raw_candidates
                    )
                    activities, _ = auto_curate_candidates(
                        enriched_candidates, city.name, city.country
                    )
                    held_for_review = save_review_candidates(
                        enriched_candidates, city.name, city.country
                    )
                    published_ids = {activity.id for activity in load_curated_activities()}
                    ready = [activity for activity in activities if activity.id not in published_ids]
                    added, duplicates = save_activities(ready)
                st.success(
                    f"Published {len(added)} quality-approved attraction(s). "
                    f"Skipped {len(duplicates)} existing record(s) and sent "
                    f"{held_for_review} ambiguous place(s) to Needs review."
                )
                _filter_published_catalog(city.country, city.name)
            except CatalogImportError as error:
                st.error(str(error), icon=":material/cloud_off:")
            except Exception as error:  # noqa: BLE001 - remote persistence must not crash the UI
                _show_catalog_write_error(error)
def _review_table_rows(records: list[dict[str, object]]) -> list[dict[str, str]]:
    """Present stored source records without exposing storage details to curators."""

    rows: list[dict[str, str]] = []
    for record in records:
        candidate = record.get("candidate_json", {})
        candidate = candidate if isinstance(candidate, dict) else {}
        source_types = candidate.get("wikidata_types", [])
        rows.append(
            {
                "Name": str(candidate.get("name", "Unnamed place")),
                "City": str(record.get("city", "")),
                "Country": str(record.get("country", "")),
                "Types": ", ".join(str(item) for item in source_types),
                "Why it needs review": str(record.get("reason", "Needs context")),
            }
        )
    return rows


def _selected_review_records(
    records: list[dict[str, object]], selected_rows: list[int]
) -> list[dict[str, object]]:
    """Return valid review selections, safely ignoring stale table row positions."""

    return [
        records[index]
        for index in selected_rows
        if isinstance(index, int) and 0 <= index < len(records)
    ]


def _render_needs_review() -> None:
    """Allow an admin to resolve the small queue of ambiguous source places."""

    completed_action = st.session_state.pop("needs-review-completed-action", None)
    if completed_action:
        st.toast(completed_action, icon=":material/check_circle:", duration=3)

    records = load_review_candidates()
    st.subheader("Needs review")
    st.caption(
        "Ambiguous places stay out of traveler results until you approve them. "
        "Clear non-visitor records are filtered out automatically."
    )
    if not records:
        st.info("No places currently need review.", icon=":material/task_alt:")
        return

    countries = sorted({str(record.get("country", "")) for record in records}, key=str.casefold)
    country = st.selectbox(
        "Country filter", ["All countries", *countries], key="review-country-filter"
    )
    cities = sorted(
        {
            str(record.get("city", ""))
            for record in records
            if country == "All countries" or record.get("country") == country
        },
        key=str.casefold,
    )
    city = st.selectbox("City filter", ["All cities", *cities], key="review-city-filter")
    filtered = [
        record
        for record in records
        if (country == "All countries" or record.get("country") == country)
        and (city == "All cities" or record.get("city") == city)
    ]
    event = st.dataframe(
        pd.DataFrame(_review_table_rows(filtered)),
        hide_index=True,
        on_select="rerun",
        selection_mode="multi-row",
        key="review-candidate-table",
    )
    selected = _selected_review_records(filtered, event.selection.rows)
    st.subheader("Resolve selection")
    approve_tab, dismiss_tab = st.tabs(
        ["Approve to published", "Dismiss from review"],
        key="review-candidate-actions",
        on_change="rerun",
    )
    if approve_tab.open:
        with approve_tab:
            st.caption(
                "Approval creates conservative, Pydantic-valid activity drafts. "
                "You can fine-tune tags and planning details later in Published activities."
            )
            if st.button(
                f"Approve {len(selected)} selected place(s)",
                icon=":material/published_with_changes:",
                type="primary",
                disabled=not selected,
                key="review-candidate-approve",
            ):
                approved = 0
                resolved_ids: list[str] = []
                try:
                    for record in selected:
                        candidate = record.get("candidate_json", {})
                        candidate = candidate if isinstance(candidate, dict) else {}
                        drafts, skipped = review_curate_candidates(
                            [candidate], str(record.get("city", "")), str(record.get("country", ""))
                        )
                        if skipped or not drafts:
                            continue
                        added, _ = save_activities(drafts)
                        approved += len(added)
                        review_id = str(record.get("review_id", ""))
                        if review_id:
                            resolved_ids.append(review_id)
                    delete_review_candidates(resolved_ids)
                    st.session_state["needs-review-completed-action"] = (
                        f"Approved {approved} place(s) and removed {len(resolved_ids)} "
                        "resolved record(s) from Needs review."
                    )
                    st.rerun()
                except Exception as error:  # noqa: BLE001 - remote persistence must not crash the UI
                    _show_catalog_write_error(error)
    if dismiss_tab.open:
        with dismiss_tab:
            selection_signature = "-".join(
                sorted(str(record.get("review_id", "")) for record in selected)
            ) or "none"
            st.caption("Dismissal removes only the review-queue record; no published activity is changed.")
            confirmed = st.checkbox(
                "I reviewed the selected places and want to dismiss them from Needs review.",
                key=f"confirm-review-dismiss-{selection_signature}",
                disabled=not selected,
            )
            if st.button(
                "Dismiss selected places",
                icon=":material/delete:",
                type="primary",
                disabled=not selected or not confirmed,
                key="review-candidate-dismiss",
            ):
                try:
                    removed = delete_review_candidates(
                        [str(record.get("review_id", "")) for record in selected]
                    )
                    st.session_state["needs-review-completed-action"] = (
                        f"Dismissed {removed} place(s) from Needs review."
                    )
                    st.rerun()
                except Exception as error:  # noqa: BLE001 - remote persistence must not crash the UI
                    _show_catalog_write_error(error)


def render_catalog_workspace() -> None:
    """Render separate views for live activities and fetched source candidates."""

    if not _catalog_access_allowed():
        return
    st.markdown('<div class="ts-section-label">Build the catalog</div>', unsafe_allow_html=True)
    st.title("Curate places your travelers will love")
    st.caption("Manage published activities, resolve ambiguous places, and fetch new source candidates.")
    published_tab, quality_tab, review_tab, fetch_tab = st.tabs(
        [
            "Published activities",
            "Data quality",
            "Needs review",
            "Fetch & batch curate",
        ],
        key="catalog-workspace-tabs",
        on_change="rerun",
    )
    if published_tab.open:
        with published_tab:
            _render_published_catalog()
    if quality_tab.open:
        with quality_tab:
            _render_catalog_quality()
    if review_tab.open:
        with review_tab:
            _render_needs_review()
    if fetch_tab.open:
        with fetch_tab:
            _render_catalog_import()
