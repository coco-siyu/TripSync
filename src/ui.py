"""Streamlit preference flow and presentation helpers."""

from __future__ import annotations

import hashlib
import json
import os
from html import escape
from pathlib import Path
from typing import Any
from uuid import uuid4

import streamlit as st
from pydantic import ValidationError

from src.catalog import (
    load_catalog_destinations,
    load_curated_activities,
    load_packaged_catalog_destinations,
)
from src.feedback import (
    FeedbackRating,
    FeedbackTargetType,
    feedback_target_id,
    record_feedback,
    record_overall_experience_feedback,
)
from src.llm import (
    NarrationConfigurationError,
    NarrationGenerationError,
    generate_itinerary_change_proposals,
    generate_itinerary_narrative,
)
from src.models import (
    Activity,
    ItineraryPlan,
    ItinerarySource,
    RejectedActivity,
    RejectionReason,
    TravelerProfile,
    TripRequest,
)
from src.must_dos import UnmatchedMustDo, resolve_must_dos
from src.narration import (
    ItineraryNarrative,
    NarrationGroundingError,
    validate_narrative_against_plan,
)
from src.planner import (
    apply_itinerary_change_proposal,
    build_itinerary,
    replace_itinerary_activity,
    route_summary_for_day,
)
from src.proposals import (
    ItineraryChangeProposal,
    ItineraryChangeProposals,
    ProposalGroundingError,
    validate_proposals_against_plan,
)
from src.scoring import GroupFitResult, rank_activities
from src.search import (
    RetrievalMode,
    RetrievalResponse,
    RetrievedActivity,
    retrieve_activities,
)
from src.trips import list_saved_trips, save_shared_itinerary_version, save_trip


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RETRIEVAL_MODE: RetrievalMode = (
    os.getenv("TRIPSYNC_RETRIEVAL_MODE", "hybrid").strip().lower()
    if os.getenv("TRIPSYNC_RETRIEVAL_MODE", "hybrid").strip().lower()
    in {"text", "vector", "hybrid"}
    else "hybrid"
)

INTEREST_OPTIONS = [
    "ancient rome",
    "architecture",
    "archaeology",
    "art",
    "culture",
    "cycling",
    "food",
    "history",
    "local culture",
    "nature",
    "photography",
    "religion",
    "relaxation",
    "sculpture",
    "shopping",
]

FOOD_RESTRICTION_OPTIONS = [
    "dairy-free",
    "gluten-free",
    "halal",
    "kosher",
    "nut allergy",
    "vegan",
    "vegetarian",
]

STEP_META = {
    "trip": ("01", "Trip basics", 33),
    "travelers": ("02", "Your group", 66),
    "results": ("03", "Best fits", 100),
}

CATEGORY_ICONS = {
    "archaeological_site": "🏺",
    "food_market": "🍋",
    "historic_site": "🏛️",
    "landmark": "📸",
    "museum": "🎨",
    "neighborhood": "✨",
    "outdoor": "🚲",
    "park": "🌿",
}
SAVED_TRIP_CONFIRMATION_KEY = "saved_trip_confirmation"


def parse_tag_text(value: str) -> list[str]:
    """Turn comma-separated user text into clean, unique tags."""

    seen: set[str] = set()
    tags: list[str] = []
    for item in value.split(","):
        tag = item.strip().lower()
        if tag and tag not in seen:
            tags.append(tag)
            seen.add(tag)
    return tags


def combine_interest_tags(*groups: list[str]) -> list[str]:
    """Combine preset and typed interests into one normalized, unique list."""

    seen: set[str] = set()
    interests: list[str] = []
    for group in groups:
        for value in group:
            normalized = value.strip().lower()
            if normalized and normalized not in seen:
                interests.append(normalized)
                seen.add(normalized)
    return interests


def format_duration(hours: float) -> str:
    """Format activity duration with correct singular/plural grammar."""

    label = f"{hours:g}"
    unit = "hour" if hours == 1 else "hours"
    return f"{label} {unit}"


def catalog_destination_options(
    activities: list[Activity],
) -> dict[str, int]:
    """Return searchable catalog destinations and their activity counts."""

    destinations: dict[tuple[str, str], tuple[str, str, int]] = {}
    for activity in activities:
        key = (activity.city.casefold(), activity.country.casefold())
        city, country, count = destinations.get(
            key,
            (activity.city, activity.country, 0),
        )
        destinations[key] = (city, country, count + 1)

    ordered = sorted(
        destinations.values(),
        key=lambda item: (str(item[0]).casefold(), str(item[1]).casefold()),
    )
    return {
        f"{city}, {country}": int(count)
        for city, country, count in ordered
    }


def split_destination(value: str | None) -> tuple[str, str]:
    """Split a destination picker value into city and optional country."""

    normalized = (value or "").strip()
    if "," not in normalized:
        return normalized, ""
    city, country = normalized.rsplit(",", maxsplit=1)
    return city.strip(), country.strip()


def catalog_city_options(
    destinations: list[tuple[str, str]],
) -> dict[str, str | None]:
    """Return searchable city names and an unambiguous catalog country."""

    cities: dict[str, tuple[str, set[str]]] = {}
    for destination_city, destination_country in destinations:
        key = destination_city.casefold()
        city, countries = cities.get(key, (destination_city, set()))
        countries.add(destination_country)
        cities[key] = (city, countries)

    return {
        city: next(iter(countries)) if len(countries) == 1 else None
        for city, countries in sorted(
            cities.values(),
            key=lambda item: item[0].casefold(),
        )
    }


def build_trip_request(
    trip_basics: dict[str, Any],
    traveler_inputs: list[dict[str, Any]],
) -> TripRequest:
    """Validate UI values using the shared application models."""

    travelers = [
        TravelerProfile(
            name=traveler["name"],
            interests=traveler["interests"],
            walking_tolerance=traveler["walking_tolerance"],
            food_restrictions=traveler.get("food_restrictions", []),
            must_do_activities=parse_tag_text(
                traveler.get("must_do_activities", "")
            ),
        )
        for traveler in traveler_inputs
    ]
    return TripRequest(**trip_basics, travelers=travelers)


def build_sample_trip() -> TripRequest:
    """Create a realistic sample group for instant visual exploration."""

    return build_trip_request(
        {
            "destination": "Rome",
            "country": "Italy",
            "days": 3,
            "budget_level": "moderate",
            "pace": "balanced",
        },
        [
            {
                "name": "Coco",
                "interests": ["history", "food", "photography"],
                "walking_tolerance": "moderate",
                "food_restrictions": ["vegetarian"],
                "must_do_activities": "Colosseum",
            },
            {
                "name": "Sam",
                "interests": ["art", "architecture", "relaxation"],
                "walking_tolerance": "low",
                "food_restrictions": [],
                "must_do_activities": "Borghese Gallery",
            },
        ],
    )


@st.cache_data(ttl="2m", max_entries=1, show_spinner=False)
def load_activity_catalog() -> list[Activity]:
    """Load the canonical catalog without querying Supabase on every click."""

    return load_curated_activities()


@st.cache_data(ttl="5m", max_entries=1, show_spinner=False)
def load_destination_index() -> list[tuple[str, str]]:
    """Load the lightweight published-destination index for autocomplete."""

    return load_catalog_destinations()


@st.cache_data(max_entries=1, show_spinner=False)
def load_packaged_destination_index() -> list[tuple[str, str]]:
    """Load instant first-paint suggestions from the bundled catalog."""

    return load_packaged_catalog_destinations()


def _initialize_state() -> None:
    defaults = {
        "planner_step": "trip",
        "trip_basics": {
            "destination": "Rome",
            "country": "Italy",
            "days": 3,
            "budget_level": "moderate",
            "pace": "balanced",
        },
        "traveler_count": 2,
        "trip_request": None,
        "selected_activity_ids": [],
        "dismissed_must_do_ids": [],
        "auto_select_must_dos": True,
        "itinerary_plan": None,
        "rejected_activities": {},
        "itinerary_undo": None,
        "itinerary_notice": None,
        "itinerary_narrative": None,
        "itinerary_narration_error": None,
        "itinerary_change_proposals": None,
        "itinerary_change_error": None,
        "retrieval_cache": None,
        "activity_detail_id": None,
        "feedback_session_id": uuid4().hex,
        "saved_trip_id": None,
        "saved_trip_owner_id": None,
        "saved_trip_access_role": "owner",
        "saved_trip_read_mode": False,
        "saved_itinerary_version_id": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _reset_activity_selections() -> None:
    """Clear shortlist state when a new trip request is submitted."""

    st.session_state.selected_activity_ids = []
    st.session_state.dismissed_must_do_ids = []
    st.session_state.auto_select_must_dos = True
    st.session_state.itinerary_plan = None
    st.session_state.rejected_activities = {}
    st.session_state.itinerary_undo = None
    st.session_state.itinerary_notice = None
    st.session_state.itinerary_narrative = None
    st.session_state.itinerary_narration_error = None
    st.session_state.itinerary_change_proposals = None
    st.session_state.itinerary_change_error = None
    st.session_state.retrieval_cache = None
    st.session_state.activity_detail_id = None
    st.session_state.saved_trip_read_mode = False
    st.session_state.saved_trip_access_role = "owner"
    st.session_state.saved_itinerary_version_id = None


def _start_new_trip() -> None:
    """Reset the planner so navigation creates a distinct saved trip."""

    st.session_state.app_workspace = "Plan a trip"
    st.session_state.planner_step = "trip"
    st.session_state.trip_basics = {
        "destination": "",
        "country": "",
        "days": 3,
        "budget_level": "moderate",
        "pace": "balanced",
    }
    st.session_state.traveler_count = 2
    st.session_state.trip_request = None
    _reset_activity_selections()
    st.session_state.saved_trip_id = None
    st.session_state.saved_trip_owner_id = None
    st.session_state.saved_trip_access_role = "owner"
    st.session_state.saved_trip_read_mode = False
    st.session_state.saved_itinerary_version_id = None
    st.session_state.pop("results_view", None)
    st.session_state.pop("itinerary_auto_fill", None)
    st.session_state.pop("open_saved_itinerary", None)
    st.session_state.pop("trip_destination_choice", None)
    st.session_state.pop("trip_custom_country", None)
    st.session_state.pop("trip_destination_city", None)
    st.session_state.pop("trip_destination_country", None)
    st.session_state.pop("trip_destination_search", None)

    traveler_widget_prefixes = (
        "traveler_name_",
        "traveler_interests_",
        "traveler_custom_interests_",
        "traveler_walking_",
        "traveler_food_",
        "traveler_must_do_",
    )
    for key in tuple(st.session_state):
        if key == "traveler_count_control" or (
            isinstance(key, str) and key.startswith(traveler_widget_prefixes)
        ):
            st.session_state.pop(key, None)


def _invalidate_itinerary() -> None:
    """Discard a generated plan after its inputs change."""

    st.session_state.itinerary_plan = None
    st.session_state.itinerary_undo = None
    st.session_state.itinerary_notice = None
    st.session_state.itinerary_narrative = None
    st.session_state.itinerary_narration_error = None
    st.session_state.itinerary_change_proposals = None
    st.session_state.itinerary_change_error = None


def _sync_catalog_country(city_countries: dict[str, str | None]) -> None:
    """Fill the country for a selected catalog city, or clear a custom one."""

    selected_city = str(
        st.session_state.get("trip_destination_search", "")
    ).strip()
    matched_city = next(
        (
            city
            for city in city_countries
            if city.casefold() == selected_city.casefold()
        ),
        None,
    )
    if matched_city is None:
        st.session_state.trip_destination_country = ""
        return
    country = city_countries[matched_city]
    if country:
        st.session_state.trip_destination_country = country


def _retrieval_cache_key(
    trip: TripRequest,
    activities: list[Activity],
) -> str:
    """Identify the exact preference and catalog inputs behind a search.

    A Streamlit button click reruns the script.  Keeping this key in session
    state means toggling, adding, removing, or building an itinerary reuses
    the already-grounded retrieval response instead of calling embeddings
    again.  A changed trip or catalog creates a new key automatically.
    """

    payload = {
        "trip": trip.model_dump(mode="json"),
        "activities": [activity.model_dump(mode="json") for activity in activities],
        "mode": RETRIEVAL_MODE,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _retrieve_for_current_trip(
    trip: TripRequest,
    activities: list[Activity],
) -> RetrievalResponse:
    """Retrieve once per trip/catalog combination within this browser session."""

    cache_key = _retrieval_cache_key(trip, activities)
    cache = st.session_state.get("retrieval_cache")
    if isinstance(cache, dict) and cache.get("key") == cache_key:
        response = cache.get("response")
        if isinstance(response, RetrievalResponse):
            return response

    with st.spinner("Finding activities that fit your group…"):
        response = retrieve_activities(
            activities,
            trip,
            mode=RETRIEVAL_MODE,
        )
    st.session_state.retrieval_cache = {
        "key": cache_key,
        "response": response,
    }
    return response




def _render_hero() -> None:
    with st.container(key="hero"):
        copy_col, visual_col = st.columns([1.08, 0.92], gap="large", vertical_alignment="center")
        with copy_col:
            with st.container(key="hero-copy"):
                st.markdown(
                    '<div class="ts-brand">TripSync · Everyone gets a say</div>',
                    unsafe_allow_html=True,
                )
                st.title("Your next great trip starts together.")
                st.markdown(
                    """
                    <p class="ts-kicker">Mix everyone’s interests, pace, budget,
                    and must-dos. TripSync finds the bright spots your whole crew
                    can get excited about—without hiding the trade-offs.</p>
                    <div class="ts-hero-badges" aria-label="TripSync planning benefits">
                        <span class="ts-hero-badge">Everyone heard</span>
                        <span class="ts-hero-badge">Fair matches</span>
                        <span class="ts-hero-badge">Trade-offs explained</span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        with visual_col:
            st.markdown(
                """
                <div class="ts-hero-visual" aria-hidden="true">
                    <div class="ts-hero-arch"></div>
                    <div class="ts-hero-route"></div>
                    <div class="ts-visual-badge ts-visual-badge--top">
                        3-day trip<small>Rome · balanced pace</small>
                    </div>
                    <div class="ts-visual-badge ts-visual-badge--bottom">
                        A plan for everyone<small>interests · budget · must-dos</small>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def _render_progress() -> None:
    step = st.session_state.planner_step
    number, label, progress = STEP_META[step]
    with st.container(key="progress-card"):
        st.progress(progress, text=f"Step {number} of 03 · {label}")


def _render_trip_step() -> None:
    basics = st.session_state.trip_basics
    if "catalog_destination_index" not in st.session_state:
        st.session_state.catalog_destination_index = (
            load_packaged_destination_index()
        )
    destination_records = st.session_state.catalog_destination_index
    city_countries = catalog_city_options(destination_records)
    destination_options = list(city_countries)
    current_destination = str(basics["destination"])
    if current_destination and current_destination not in city_countries:
        destination_options.append(current_destination)
    destination_index = (
        destination_options.index(current_destination)
        if current_destination in destination_options
        else None
    )
    st.session_state.setdefault(
        "trip_destination_country",
        str(basics["country"]),
    )

    with st.container(key="trip-card"):
        st.markdown('<div class="ts-section-label">Start with the shape of the trip</div>', unsafe_allow_html=True)
        st.header("Where are you going?")
        st.markdown(
            '<p class="ts-helper">A few shared boundaries make the individual '
            "preferences easier to balance later.</p>",
            unsafe_allow_html=True,
        )

        destination_col, country_col = st.columns(2, gap="large")
        destination = destination_col.selectbox(
            "Destination city",
            destination_options,
            index=destination_index,
            key="trip_destination_search",
            placeholder="Start typing any city",
            accept_new_options=True,
            filter_mode="prefix",
            on_change=_sync_catalog_country,
            args=(city_countries,),
            help=(
                "Start typing to see catalog cities with matching names. "
                "You can also enter a city that is not listed."
            ),
        )
        country = country_col.text_input(
            "Country",
            key="trip_destination_country",
            placeholder="e.g. Italy",
        )

        with st.form("trip_basics_form"):
            days = st.slider(
                "How many days?",
                min_value=1,
                max_value=5,
                value=basics["days"],
                help="The current MVP supports one-to-five-day trips.",
            )
            budget_level = st.segmented_control(
                "Shared activity budget",
                ["free", "low", "moderate", "high"],
                default=basics["budget_level"],
                format_func=str.title,
                required=True,
                width="stretch",
            )
            pace = st.segmented_control(
                "Preferred pace",
                ["relaxed", "balanced", "packed"],
                default=basics["pace"],
                format_func=str.title,
                required=True,
                width="stretch",
            )

            submitted = st.form_submit_button(
                "Continue to travelers",
                type="primary",
                icon=":material/arrow_forward:",
                width="stretch",
            )

        if submitted:
            if not destination or not country.strip():
                st.error(
                    "Choose a destination and enter its country before continuing.",
                    icon=":material/error:",
                )
            else:
                st.session_state.trip_basics = {
                    "destination": destination,
                    "country": country,
                    "days": days,
                    "budget_level": budget_level,
                    "pace": pace,
                }
                st.session_state.planner_step = "travelers"
                st.rerun()

        if st.button(
            "Preview a sample group",
            icon=":material/visibility:",
            type="secondary",
            width="stretch",
            help="Jump to example recommendations, then return to build your own trip.",
        ):
            sample_trip = build_sample_trip()
            st.session_state.trip_basics = sample_trip.model_dump(
                mode="json",
                exclude={"travelers"},
            )
            st.session_state.trip_request = sample_trip.model_dump(mode="json")
            _reset_activity_selections()
            st.session_state.planner_step = "results"
            st.rerun()

    # Draw the complete first-step UI before making the optional network call.
    # The first session refreshes the small destination index once; subsequent
    # reruns and sessions use Streamlit's cache and never reload full activities.
    if not st.session_state.get("catalog_destination_index_refreshed"):
        refreshed_destinations = load_destination_index()
        st.session_state.catalog_destination_index_refreshed = True
        if refreshed_destinations != destination_records:
            st.session_state.catalog_destination_index = refreshed_destinations
            st.rerun()


def _traveler_input(index: int) -> dict[str, Any]:
    traveler_number = index + 1
    with st.container(key=f"traveler-card-{traveler_number}"):
        st.subheader(f"Traveler {traveler_number}")
        name = st.text_input(
            "Name",
            key=f"traveler_name_{index}",
            placeholder="Who is joining?",
        )
        interests = st.pills(
            "Interests",
            INTEREST_OPTIONS,
            selection_mode="multi",
            key=f"traveler_interests_{index}",
            help="Choose at least one interest.",
            width="stretch",
        )
        custom_interests = st.multiselect(
            "Add your own interests",
            options=[],
            key=f"traveler_custom_interests_{index}",
            placeholder="Optional · type ‘Renaissance painting’ then press Enter",
            accept_new_options=True,
            max_selections=12,
            help="Use a specific phrase when the preset interests do not describe the trip you want.",
            label_visibility="collapsed",
        )
        walking_tolerance = st.segmented_control(
            "Walking tolerance",
            ["low", "moderate", "high"],
            default="moderate",
            format_func=str.title,
            key=f"traveler_walking_{index}",
            required=True,
            width="stretch",
        )
        food_restrictions = st.multiselect(
            "Food restrictions",
            FOOD_RESTRICTION_OPTIONS,
            key=f"traveler_food_{index}",
            placeholder="Optional · choose or type your own",
            accept_new_options=True,
            max_selections=12,
        )
        must_do_activities = st.text_input(
            "Must-do activities",
            key=f"traveler_must_do_{index}",
            placeholder="Optional · Colosseum, Vatican Museums",
            help="Separate multiple activities with commas.",
        )

    return {
        "name": name,
        "interests": combine_interest_tags(interests or [], custom_interests),
        "walking_tolerance": walking_tolerance,
        "food_restrictions": food_restrictions,
        "must_do_activities": must_do_activities,
    }


def _validation_message(error: ValidationError) -> str:
    first_error = error.errors()[0]
    location = " → ".join(str(part) for part in first_error["loc"])
    return f"{location}: {first_error['msg']}"


def _render_travelers_step() -> None:
    if st.button(
        "Back to trip basics",
        icon=":material/arrow_back:",
        type="tertiary",
    ):
        st.session_state.planner_step = "trip"
        st.rerun()

    with st.container(key="travelers-shell"):
        st.markdown('<div class="ts-section-label">Make room for everyone</div>', unsafe_allow_html=True)
        st.header("Who’s traveling?")
        st.markdown(
            '<p class="ts-helper">Each profile helps TripSync distinguish a '
            "genuine group fit from a generic popular attraction.</p>",
            unsafe_allow_html=True,
        )

        traveler_count = st.slider(
            "Group size",
            min_value=2,
            max_value=6,
            value=st.session_state.traveler_count,
            key="traveler_count_control",
        )
        st.session_state.traveler_count = traveler_count

        with st.form("traveler_profiles_form"):
            traveler_inputs = [
                _traveler_input(index) for index in range(traveler_count)
            ]
            submitted = st.form_submit_button(
                "Find our best fits",
                type="primary",
                icon=":material/auto_awesome:",
                width="stretch",
            )

        if submitted:
            try:
                trip_request = build_trip_request(
                    st.session_state.trip_basics,
                    traveler_inputs,
                )
            except ValidationError as error:
                st.error(
                    _validation_message(error),
                    icon=":material/error:",
                )
            else:
                st.session_state.trip_request = trip_request.model_dump(mode="json")
                _reset_activity_selections()
                st.session_state.planner_step = "results"
                st.rerun()


def _chip_row(labels: list[str]) -> str:
    chips = "".join(
        f'<span class="ts-chip">{escape(label)}</span>' for label in labels
    )
    return f'<div class="ts-chip-row">{chips}</div>'


def _render_summary(trip: TripRequest, *, show_edit: bool = True) -> None:
    with st.container(key="summary-card"):
        if show_edit:
            heading_col, action_col = st.columns(
                [4, 1], vertical_alignment="center"
            )
        else:
            heading_col = st
            action_col = None
        heading_col.markdown('<div class="ts-section-label">Your planning brief</div>', unsafe_allow_html=True)
        heading_col.subheader(
            f"{trip.destination}, {trip.country} · {trip.days} days"
        )
        if action_col is not None and action_col.button(
            "Edit",
            icon=":material/edit:",
            type="secondary",
            width="stretch",
        ):
            st.session_state.planner_step = "trip"
            st.rerun()

        labels = [
            f"{len(trip.travelers)} travelers",
            f"{trip.budget_level.value} budget",
            f"{trip.pace.value} pace",
        ]
        st.markdown(_chip_row(labels), unsafe_allow_html=True)
        st.caption(
            " · ".join(
                f"{traveler.name}: {', '.join(traveler.interests)}"
                for traveler in trip.travelers
            )
        )


def _activity_lookup(activities: list[Activity]) -> dict[str, Activity]:
    return {activity.id: activity for activity in activities}


def _owner_names(owners: tuple[str, ...]) -> str:
    if len(owners) == 1:
        return owners[0]
    return " + ".join(owners)


def _sync_initial_must_dos(must_do_ids: list[str]) -> None:
    """Add new must-dos once while respecting deliberate removals."""

    if not st.session_state.auto_select_must_dos:
        return

    selected = list(st.session_state.selected_activity_ids)
    dismissed = set(st.session_state.dismissed_must_do_ids)
    for activity_id in must_do_ids:
        if activity_id not in selected and activity_id not in dismissed:
            selected.append(activity_id)
    st.session_state.selected_activity_ids = selected


def _rejected_activity_records() -> dict[str, RejectedActivity]:
    return {
        activity_id: RejectedActivity.model_validate(payload)
        for activity_id, payload in st.session_state.rejected_activities.items()
    }


def _excluded_activity_ids() -> list[str]:
    return list(
        dict.fromkeys(
            [
                *st.session_state.dismissed_must_do_ids,
                *st.session_state.rejected_activities,
            ]
        )
    )


def _add_activity_to_shortlist(activity_id: str) -> None:
    selected = list(st.session_state.selected_activity_ids)
    if activity_id not in selected:
        selected.append(activity_id)
    st.session_state.selected_activity_ids = selected
    st.session_state.dismissed_must_do_ids = [
        dismissed_id
        for dismissed_id in st.session_state.dismissed_must_do_ids
        if dismissed_id != activity_id
    ]
    _invalidate_itinerary()


def _restore_rejected_activity(activity_id: str) -> None:
    rejected = dict(st.session_state.rejected_activities)
    rejected.pop(activity_id, None)
    st.session_state.rejected_activities = rejected
    _add_activity_to_shortlist(activity_id)


def _remove_activity_from_shortlist(
    activity_id: str,
    *,
    is_must_do: bool,
) -> None:
    st.session_state.selected_activity_ids = [
        selected_id
        for selected_id in st.session_state.selected_activity_ids
        if selected_id != activity_id
    ]
    if (
        is_must_do
        and activity_id not in st.session_state.dismissed_must_do_ids
    ):
        st.session_state.dismissed_must_do_ids = [
            *st.session_state.dismissed_must_do_ids,
            activity_id,
        ]
    _invalidate_itinerary()


def _render_unmatched_must_do(entry: UnmatchedMustDo) -> None:
    message = (
        f'{entry.traveler_name}’s must-do “{entry.entered_value}” '
        "did not match a known activity."
    )
    if entry.suggested_activity_name:
        message += f" Did you mean {entry.suggested_activity_name}?"
    message += " Edit the traveler profile to correct it."
    st.warning(message, icon=":material/search_off:")


def _render_activity_action(
    activity: Activity,
    *,
    is_must_do: bool,
    key_prefix: str = "",
) -> None:
    selected = activity.id in st.session_state.selected_activity_ids
    label = "Remove from trip" if selected else "Add to trip"
    icon = ":material/remove_circle:" if selected else ":material/add_circle:"
    if st.button(
        label,
        key=f"{key_prefix}activity-selection-{activity.id}",
        icon=icon,
        type="secondary" if selected else "primary",
        width="stretch",
    ):
        if selected:
            _remove_activity_from_shortlist(
                activity.id,
                is_must_do=is_must_do,
            )
            st.toast(f"Removed {activity.name} from your trip.")
        else:
            _add_activity_to_shortlist(activity.id)
            st.toast(f"Added {activity.name} to your trip.")
        st.rerun()


def _render_activity_details(
    activity: Activity,
) -> None:
    """Render the additional, practical visit information within a result card."""

    with st.container(border=True, key=f"activity-detail-{activity.id}"):
        st.markdown("**Visit information**")
        left, right = st.columns(2)
        with left:
            st.markdown(f"**Typical visit:** {format_duration(activity.duration_hours)}")
            st.markdown(f"**Walking:** {activity.walking_level.value.title()}")
            st.markdown(f"**Budget:** {activity.budget_level.value.title()}")
            st.markdown(
                "**Reservations:** "
                f"{'Usually recommended' if activity.reservation_required else 'Not usually required'}"
            )
        with right:
            st.markdown(f"**Setting:** {'Indoors' if activity.indoor else 'Mostly outdoors'}")
            st.markdown(
                "**Family-friendly:** "
                f"{'Yes' if activity.family_friendly else 'Check before visiting'}"
            )
            st.markdown(f"**Address:** {activity.address or 'Not available yet'}")
            if activity.official_hours_url:
                st.markdown("**Opening hours:** See the official hours page")
            else:
                st.markdown(
                    "**Opening hours:** "
                    f"{activity.opening_hours or 'Not available yet'}"
                )

        st.caption(
            "Opening hours and access can change. Confirm them with the "
            "official visitor source before you go."
        )
        with st.container(horizontal=True):
            if activity.official_site_verified and activity.official_url:
                st.link_button(
                    "Official site",
                    str(activity.official_url),
                    icon=":material/verified:",
                )
            if activity.official_hours_url:
                st.link_button(
                    "Official hours",
                    str(activity.official_hours_url),
                    icon=":material/schedule:",
                )
            if activity.official_tickets_url:
                st.link_button(
                    "Official tickets",
                    str(activity.official_tickets_url),
                    icon=":material/confirmation_number:",
                )
            if activity.latitude is not None and activity.longitude is not None:
                map_url = (
                    "https://www.google.com/maps/search/?api=1&query="
                    f"{activity.latitude},{activity.longitude}"
                )
                st.link_button(
                    "Open map", map_url, icon=":material/location_on:"
                )


def _render_result_card(
    rank: int | None,
    result: GroupFitResult,
    activity: Activity,
    retrieval: RetrievedActivity,
    must_do_owners: tuple[str, ...] = (),
    rejection: RejectedActivity | None = None,
) -> None:
    card_position = (
        rank
        if rank is not None
        else "rejected" if rejection is not None else "must-do"
    )
    with st.container(
        key=f"result-card-{card_position}-{result.activity_id}"
    ):
        score_col, content_col = st.columns([1, 4], gap="large")
        with score_col:
            if rank is not None:
                rank_label = f"#{rank} group fit"
            elif rejection is not None:
                rank_label = "Previous group fit"
            else:
                rank_label = "Group fit score"
            st.caption(rank_label)
            st.markdown(
                f'<div class="ts-score"><strong>{result.total_score:.0f}</strong>'
                "<span>/100</span></div>",
                unsafe_allow_html=True,
            )
            st.progress(
                int(result.total_score),
                text=f"{result.coverage_count}/{len(result.traveler_fits)} travelers covered",
            )

        with content_col:
            category_icon = CATEGORY_ICONS.get(activity.category, "🧭")
            st.subheader(f"{category_icon} {activity.name}")
            st.caption(
                f"{activity.category.replace('_', ' ').title()} · "
                f"{format_duration(activity.duration_hours)} · "
                f"{activity.walking_level.value.title()} walking · "
                f"{activity.budget_level.value.title()} budget"
            )
            if must_do_owners:
                owner_names = escape(_owner_names(must_do_owners))
                st.markdown(
                    '<div class="ts-must-do-line">★ Must-do for '
                    f"{owner_names}</div>",
                    unsafe_allow_html=True,
                )
            if rejection is not None:
                rejection_detail = f"Rejected: {rejection.reason.value}"
                if rejection.note:
                    rejection_detail += f" · {rejection.note}"
                st.markdown(
                    '<div class="ts-rejected-line">'
                    f"{escape(rejection_detail)}</div>",
                    unsafe_allow_html=True,
                )
            st.write(activity.description)

            matched = sorted(
                {
                    interest
                    for fit in result.traveler_fits
                    for interest in fit.matched_interests
                }
            )
            if matched:
                st.markdown(
                    _chip_row([f"Matches {interest}" for interest in matched]),
                    unsafe_allow_html=True,
                )

            for tradeoff in result.tradeoffs:
                st.markdown(
                    f'<div class="ts-tradeoff">{escape(tradeoff)}</div>',
                    unsafe_allow_html=True,
                )

            details_are_open = st.session_state.activity_detail_id == activity.id
            if st.button(
                "Hide details" if details_are_open else "View details",
                key=f"view-activity-details-{card_position}-{activity.id}",
                icon=(
                    ":material/visibility_off:"
                    if details_are_open
                    else ":material/visibility:"
                ),
                type="secondary",
                width="stretch",
            ):
                st.session_state.activity_detail_id = (
                    None if details_are_open else activity.id
                )
                st.rerun()

            if st.session_state.activity_detail_id == activity.id:
                _render_activity_details(activity)

            with st.expander("Why this fits your group"):
                st.markdown("**Why it was retrieved**")
                for reason in retrieval.reasons:
                    st.caption(reason)

                st.markdown("**How it fits each traveler**")
                for fit in result.traveler_fits:
                    status = (
                        "✓"
                        if fit.matched_interests
                        or fit.semantic_interest_score
                        or fit.must_do_match
                        else "○"
                    )
                    st.markdown(
                        f"**{status} {fit.traveler_name} · {fit.score:.0f}/100**"
                    )
                    for explanation in fit.explanations:
                        st.caption(explanation)

                st.caption(activity.accessibility_notes)

            if rejection is not None:
                if st.button(
                    "Restore to shortlist",
                    key=f"restore-rejected-{activity.id}",
                    icon=":material/restore:",
                    type="primary",
                    width="stretch",
                ):
                    _restore_rejected_activity(activity.id)
                    st.toast(f"Restored {activity.name} to your shortlist.")
                    st.rerun()
            else:
                _render_activity_action(
                    activity,
                    is_must_do=bool(must_do_owners),
                )


def _itinerary_slot_label(position: int, total: int) -> str:
    if total == 1:
        return "Flexible highlight"
    labels = {
        2: ("Morning", "Afternoon"),
        3: ("Morning", "Midday", "Afternoon"),
        4: ("Morning", "Late morning", "Afternoon", "Evening"),
    }
    day_labels = labels.get(total, ())
    if position < len(day_labels):
        return day_labels[position]
    return f"Stop {position + 1}"


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _reject_and_replace_activity(
    plan: ItineraryPlan,
    activity: Activity,
    reason: RejectionReason,
    note: str,
    candidate_activities: list[Activity],
    ranked_results: list[GroupFitResult],
    owners_by_activity_id: dict[str, tuple[str, ...]],
) -> None:
    previous_state = {
        "itinerary_plan": _json_copy(
            st.session_state.itinerary_plan
        ),
        "rejected_activities": _json_copy(
            st.session_state.rejected_activities
        ),
        "selected_activity_ids": list(
            st.session_state.selected_activity_ids
        ),
        "dismissed_must_do_ids": list(
            st.session_state.dismissed_must_do_ids
        ),
        "itinerary_narrative": _json_copy(
            st.session_state.itinerary_narrative
        ),
        "itinerary_narration_error": st.session_state.itinerary_narration_error,
    }
    outcome = replace_itinerary_activity(
        plan,
        activity.id,
        candidate_activities,
        ranked_results,
        excluded_activity_ids=[
            *_excluded_activity_ids(),
            activity.id,
        ],
        must_do_owners_by_activity_id=owners_by_activity_id,
    )
    rejection = RejectedActivity(
        activity_id=activity.id,
        activity_name=activity.name,
        reason=reason,
        note=note.strip() or None,
        day_number=outcome.day_number,
    )
    rejected = dict(st.session_state.rejected_activities)
    rejected[activity.id] = rejection.model_dump(mode="json")

    st.session_state.itinerary_undo = previous_state
    st.session_state.rejected_activities = rejected
    st.session_state.selected_activity_ids = [
        selected_id
        for selected_id in st.session_state.selected_activity_ids
        if selected_id != activity.id
    ]
    if (
        owners_by_activity_id.get(activity.id)
        and activity.id not in st.session_state.dismissed_must_do_ids
    ):
        st.session_state.dismissed_must_do_ids = [
            *st.session_state.dismissed_must_do_ids,
            activity.id,
        ]
    st.session_state.itinerary_plan = outcome.plan.model_dump(mode="json")
    st.session_state.itinerary_narrative = None
    st.session_state.itinerary_narration_error = None

    if outcome.replacement_activity is None:
        st.session_state.itinerary_notice = (
            f"{activity.name} was rejected, but no unused activity could "
            f"fit Day {outcome.day_number} without breaking its pace limits. "
            "The time remains open."
        )
    else:
        st.session_state.itinerary_notice = (
            f"{activity.name} was replaced with "
            f"{outcome.replacement_activity.activity_name} on "
            f"Day {outcome.day_number}. Every other day stayed unchanged."
        )


def _undo_last_replacement() -> None:
    snapshot = st.session_state.itinerary_undo
    if not snapshot:
        return
    for key, value in snapshot.items():
        st.session_state[key] = _json_copy(value)
    st.session_state.itinerary_undo = None
    st.session_state.itinerary_notice = "The last replacement was undone."


def _load_grounded_narrative(
    plan: ItineraryPlan,
) -> ItineraryNarrative | None:
    """Return the saved story only when it still matches this plan."""

    payload = st.session_state.itinerary_narrative
    if payload is None:
        return None

    try:
        narrative = ItineraryNarrative.model_validate(payload)
        return validate_narrative_against_plan(narrative, plan)
    except (ValidationError, NarrationGroundingError):
        st.session_state.itinerary_narrative = None
        st.session_state.itinerary_narration_error = (
            "The saved trip story no longer matches this itinerary. "
            "Generate it again after reviewing the schedule."
        )
        return None


def _render_narration(
    trip: TripRequest,
    plan: ItineraryPlan,
    activity_by_id: dict[str, Activity],
) -> ItineraryNarrative | None:
    """Render optional, grounded LLM narration for an immutable itinerary."""

    narrative = _load_grounded_narrative(plan)
    with st.container(border=True):
        st.markdown(
            '<div class="ts-section-label">Optional trip story</div>',
            unsafe_allow_html=True,
        )
        st.subheader("Bring the itinerary to life")
        st.caption(
            "TripSync keeps the schedule fixed, then uses your selected "
            "activities and preferences to explain why it fits the group."
        )
        if st.button(
            "Generate trip story",
            key="generate-itinerary-narrative",
            icon=":material/auto_awesome:",
            type="secondary",
            width="stretch",
        ):
            st.session_state.itinerary_narration_error = None
            try:
                with st.status(
                    "Writing a grounded trip story…",
                    expanded=True,
                    state="running",
                ) as status:
                    st.write("Using only the activities already scheduled.")
                    generated = generate_itinerary_narrative(
                        trip,
                        list(activity_by_id.values()),
                        plan,
                    )
                    st.session_state.itinerary_narrative = generated.model_dump(
                        mode="json"
                    )
                    status.update(
                        label="Trip story ready",
                        state="complete",
                        expanded=False,
                    )
                st.rerun()
            except (
                NarrationConfigurationError,
                NarrationGenerationError,
            ) as error:
                st.session_state.itinerary_narration_error = str(error)
                st.rerun()

        if st.session_state.itinerary_narration_error:
            st.info(
                st.session_state.itinerary_narration_error,
                icon=":material/info:",
            )

        if narrative is not None:
            st.write(narrative.trip_summary)
            if narrative.overall_tradeoffs:
                with st.expander("Planning notes", expanded=False):
                    for tradeoff in narrative.overall_tradeoffs:
                        st.markdown(f"- {tradeoff}")
            _render_llm_feedback(
                target_type="trip_story",
                payload=narrative.model_dump(mode="json"),
            )
    return narrative


def _render_llm_feedback(
    *,
    target_type: FeedbackTargetType,
    payload: dict[str, Any],
) -> None:
    """Render local thumbs feedback without storing trip data or LLM output."""

    target_id = feedback_target_id(target_type, payload)
    st.caption(
        "Saved locally on this computer. Please do not include private details."
    )
    rating_key = f"saved-feedback-rating-{target_id}"

    def save_rating(rating: FeedbackRating, comment: str | None = None) -> None:
        record_feedback(
            session_id=st.session_state.feedback_session_id,
            target_type=target_type,
            target_id=target_id,
            rating=rating,
            comment=comment,
        )
        st.session_state[rating_key] = rating

    st.caption("Was this helpful? Your choice saves immediately.")
    helpful_col, not_useful_col = st.columns(2)
    if helpful_col.button(
        "Helpful",
        key=f"feedback-helpful-{target_id}",
        icon=":material/thumb_up:",
        type="secondary",
        width="stretch",
    ):
        save_rating("up")
        st.toast("Marked helpful and saved locally.")
    if not_useful_col.button(
        "Not useful",
        key=f"feedback-not-useful-{target_id}",
        icon=":material/thumb_down:",
        type="secondary",
        width="stretch",
    ):
        save_rating("down")
        st.toast("Marked not useful and saved locally.")

    comment = st.text_area(
        "Optional feedback comment",
        placeholder="What worked or what should change?",
        max_chars=1_000,
        key=f"feedback-comment-{target_id}",
    )
    saved_rating = st.session_state.get(rating_key)
    if saved_rating:
        st.caption(
            "Your rating is saved. Add a comment below only if you want to "
            "update it."
        )
    if st.button(
        "Save comment",
        key=f"feedback-comment-save-{target_id}",
        icon=":material/rate_review:",
        type="tertiary",
        width="stretch",
        disabled=saved_rating is None,
    ):
        save_rating(saved_rating, comment)
        st.toast("Your comment was saved locally.")


def _render_overall_experience_feedback(
    trip: TripRequest,
    plan: ItineraryPlan,
) -> None:
    """Render the compact human-quality rubric for a completed itinerary."""

    itinerary_id = feedback_target_id(
        "overall_experience",
        {
            "trip": trip.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json"),
        },
    )
    with st.container(border=True):
        st.markdown(
            '<div class="ts-section-label">Overall experience</div>',
            unsafe_allow_html=True,
        )
        st.subheader("How did TripSync do?")
        st.caption(
            "Three quick ratings help us improve the experience. "
            "Saved locally on this computer."
        )
        with st.form(f"overall-feedback-form-{itinerary_id}", border=False):
            helpfulness = st.segmented_control(
                "Helpful for planning",
                range(1, 6),
                format_func=lambda score: f"{score} ★",
                selection_mode="single",
                key=f"overall-feedback-helpfulness-{itinerary_id}",
                width="stretch",
            )
            clarity = st.segmented_control(
                "Clear and easy to use",
                range(1, 6),
                format_func=lambda score: f"{score} ★",
                selection_mode="single",
                key=f"overall-feedback-clarity-{itinerary_id}",
                width="stretch",
            )
            group_fit = st.segmented_control(
                "Fits your group",
                range(1, 6),
                format_func=lambda score: f"{score} ★",
                selection_mode="single",
                key=f"overall-feedback-group-fit-{itinerary_id}",
                width="stretch",
            )
            comment = st.text_area(
                "Optional overall feedback",
                placeholder="Anything we could improve?",
                max_chars=1_000,
                key=f"overall-feedback-comment-{itinerary_id}",
            )
            submitted = st.form_submit_button(
                "Save overall feedback",
                icon=":material/rate_review:",
                type="secondary",
                width="stretch",
            )
        if submitted:
            if None in (helpfulness, clarity, group_fit):
                st.warning("Choose a 1–5 rating for all three questions.")
                return
            record_overall_experience_feedback(
                session_id=st.session_state.feedback_session_id,
                itinerary_id=itinerary_id,
                helpfulness=helpfulness,
                clarity=clarity,
                group_fit=group_fit,
                comment=comment,
            )
            st.toast("Thanks — your overall feedback was saved locally.")


def _load_change_proposals(
    plan: ItineraryPlan,
    eligible_activities: list[Activity],
) -> ItineraryChangeProposals | None:
    """Return saved options only when they still match this itinerary."""

    payload = st.session_state.itinerary_change_proposals
    if payload is None:
        return None
    try:
        proposals = ItineraryChangeProposals.model_validate(payload)
        return validate_proposals_against_plan(
            proposals,
            plan,
            eligible_activities,
        )
    except ValidationError:
        st.session_state.itinerary_change_proposals = None
        st.session_state.itinerary_change_error = (
            "Those suggestions no longer match this itinerary. Ask again "
            "after reviewing the updated plan."
        )
        return None
    except ProposalGroundingError:
        st.session_state.itinerary_change_proposals = None
        st.session_state.itinerary_change_error = (
            "Those suggestions no longer match this itinerary. Ask again "
            "after reviewing the updated plan."
        )
        return None


def _apply_change_proposal(
    plan: ItineraryPlan,
    proposal: ItineraryChangeProposal,
    candidate_activities: list[Activity],
    ranked_results: list[GroupFitResult],
    owners_by_activity_id: dict[str, tuple[str, ...]],
    *,
    allow_pace_override: bool = False,
) -> None:
    """Apply an organizer-approved proposal and retain an undo snapshot."""

    previous_state = {
        "itinerary_plan": _json_copy(st.session_state.itinerary_plan),
        "rejected_activities": _json_copy(
            st.session_state.rejected_activities
        ),
        "selected_activity_ids": list(
            st.session_state.selected_activity_ids
        ),
        "dismissed_must_do_ids": list(
            st.session_state.dismissed_must_do_ids
        ),
        "itinerary_narrative": _json_copy(
            st.session_state.itinerary_narrative
        ),
        "itinerary_narration_error": st.session_state.itinerary_narration_error,
        "itinerary_change_proposals": _json_copy(
            st.session_state.itinerary_change_proposals
        ),
        "itinerary_change_error": st.session_state.itinerary_change_error,
    }
    outcome = apply_itinerary_change_proposal(
        plan,
        proposal,
        candidate_activities,
        ranked_results,
        must_do_owners_by_activity_id=owners_by_activity_id,
        allow_pace_override=allow_pace_override,
    )
    rejected = dict(st.session_state.rejected_activities)
    if outcome.removed_activity is not None:
        rejected[outcome.removed_activity.activity_id] = RejectedActivity(
            activity_id=outcome.removed_activity.activity_id,
            activity_name=outcome.removed_activity.activity_name,
            reason=RejectionReason.OTHER,
            note=f"Organizer accepted suggestion: {proposal.title}",
            day_number=outcome.day_number,
        ).model_dump(mode="json")
    st.session_state.itinerary_undo = previous_state
    st.session_state.rejected_activities = rejected
    if outcome.removed_activity is not None:
        st.session_state.selected_activity_ids = [
            activity_id
            for activity_id in st.session_state.selected_activity_ids
            if activity_id != outcome.removed_activity.activity_id
        ]
        if (
            owners_by_activity_id.get(outcome.removed_activity.activity_id)
            and outcome.removed_activity.activity_id
            not in st.session_state.dismissed_must_do_ids
        ):
            st.session_state.dismissed_must_do_ids = [
                *st.session_state.dismissed_must_do_ids,
                outcome.removed_activity.activity_id,
            ]
    if outcome.replacement_activity is not None:
        if (
            outcome.replacement_activity.activity_id
            not in st.session_state.selected_activity_ids
        ):
            st.session_state.selected_activity_ids = [
                *st.session_state.selected_activity_ids,
                outcome.replacement_activity.activity_id,
            ]
    st.session_state.itinerary_plan = outcome.plan.model_dump(mode="json")
    st.session_state.itinerary_narrative = None
    st.session_state.itinerary_narration_error = None
    st.session_state.itinerary_change_proposals = None
    st.session_state.itinerary_change_error = None
    if outcome.removed_activity is None and outcome.replacement_activity is not None:
        st.session_state.itinerary_notice = (
            f"Added {outcome.replacement_activity.activity_name} to Day "
            f"{outcome.day_number}."
        )
    elif outcome.replacement_activity is None:
        st.session_state.itinerary_notice = (
            f"Removed {outcome.removed_activity.activity_name} from Day "
            f"{outcome.day_number}; the time is now open."
        )
    else:
        st.session_state.itinerary_notice = (
            f"Replaced {outcome.removed_activity.activity_name} with "
            f"{outcome.replacement_activity.activity_name} on Day "
            f"{outcome.day_number}."
        )


def _render_change_proposals(
    trip: TripRequest,
    plan: ItineraryPlan,
    candidate_activities: list[Activity],
    ranked_results: list[GroupFitResult],
    owners_by_activity_id: dict[str, tuple[str, ...]],
) -> None:
    """Render a request-and-review flow for grounded plan adjustments."""

    scheduled_ids = {
        activity.activity_id for day in plan.days for activity in day.activities
    }
    excluded_ids = set(_excluded_activity_ids())
    eligible_activities = [
        activity
        for activity in candidate_activities
        if activity.id not in scheduled_ids and activity.id not in excluded_ids
    ]
    proposals = _load_change_proposals(
        plan,
        eligible_activities,
    )

    with st.container(border=True):
        st.markdown(
            '<div class="ts-section-label">Fine-tune the plan</div>',
            unsafe_allow_html=True,
        )
        st.subheader("What would you like to adjust?")
        st.caption(
            "Ask for a calmer day, less walking, more food, or a different "
            "balance. Suggestions stay grounded in this catalog and require "
            "your approval before they change anything."
        )
        with st.form("itinerary-change-request", border=False):
            request = st.text_input(
                "Adjustment request",
                placeholder="For example: Make Day 2 calmer with more food.",
                key="itinerary-change-request-text",
                max_chars=500,
            )
            submitted = st.form_submit_button(
                "Suggest adjustments",
                icon=":material/tune:",
                type="secondary",
                width="stretch",
            )
        if submitted:
            st.session_state.itinerary_change_error = None
            try:
                with st.status(
                    "Finding grounded adjustment ideas…",
                    expanded=True,
                    state="running",
                ) as status:
                    st.write("Keeping the existing day structure and pace limits.")
                    generated = generate_itinerary_change_proposals(
                        trip,
                        eligible_activities,
                        plan,
                        request,
                    )
                    st.session_state.itinerary_change_proposals = (
                        generated.model_dump(mode="json")
                    )
                    status.update(
                        label="Adjustment ideas ready",
                        state="complete",
                        expanded=False,
                    )
                st.rerun()
            except (
                NarrationConfigurationError,
                NarrationGenerationError,
            ) as error:
                st.session_state.itinerary_change_error = str(error)
                st.rerun()

        if st.session_state.itinerary_change_error:
            st.info(
                st.session_state.itinerary_change_error,
                icon=":material/info:",
            )
        if proposals is not None:
            st.write(proposals.acknowledgement)
            for index, proposal in enumerate(proposals.proposals, start=1):
                with st.container(border=True):
                    st.markdown(f"**Option {index}: {proposal.title}**")
                    if proposal.operation == "add":
                        action_description = (
                            f"Day {proposal.day_number} · Add "
                            f"`{proposal.add_activity_id}`"
                        )
                    elif proposal.operation == "remove":
                        action_description = (
                            f"Day {proposal.day_number} · Leave time open "
                            f"after removing `{proposal.remove_activity_id}`"
                        )
                    else:
                        action_description = (
                            f"Day {proposal.day_number} · Replace "
                            f"`{proposal.remove_activity_id}` with "
                            f"`{proposal.add_activity_id}`"
                        )
                    st.caption(action_description)
                    st.write(proposal.rationale)
                    for tradeoff in proposal.tradeoffs:
                        st.caption(f"Trade-off: {tradeoff}")
                    _render_llm_feedback(
                        target_type="adjustment_proposal",
                        payload=proposal.model_dump(mode="json"),
                    )
                    application_error: str | None = None
                    try:
                        apply_itinerary_change_proposal(
                            plan,
                            proposal,
                            candidate_activities,
                            ranked_results,
                            must_do_owners_by_activity_id=owners_by_activity_id,
                        )
                    except ValueError as error:
                        application_error = str(error)
                    if application_error:
                        st.warning(
                            "This option exceeds the recommended pace: "
                            f"{application_error}",
                            icon=":material/schedule:",
                        )
                        override_confirmed = st.checkbox(
                            "I understand this day will exceed its "
                            "recommended pace. Apply it anyway.",
                            key=f"pace-override-confirm-{index}",
                        )
                    else:
                        override_confirmed = False
                    if st.button(
                        (
                            "Apply anyway"
                            if application_error
                            else "Apply this suggestion"
                        ),
                        key=f"apply-itinerary-proposal-{index}",
                        icon=(
                            ":material/schedule:"
                            if application_error
                            else ":material/check_circle:"
                        ),
                        type="primary",
                        width="stretch",
                        disabled=application_error is not None and not override_confirmed,
                    ):
                        try:
                            _apply_change_proposal(
                                plan,
                                proposal,
                                candidate_activities,
                                ranked_results,
                                owners_by_activity_id,
                                allow_pace_override=application_error is not None,
                            )
                            st.toast("Applied your approved itinerary change.")
                        except ValueError as error:
                            st.session_state.itinerary_change_error = str(error)
                        st.rerun()


def _save_current_trip(
    trip: TripRequest,
    *,
    save_itinerary_version: bool,
) -> Any:
    """Persist the current planning state, including an itinerary snapshot."""

    account = st.session_state.get("account_session") or {}
    planning_state = {
        "selected_activity_ids": st.session_state.selected_activity_ids,
        "dismissed_must_do_ids": st.session_state.dismissed_must_do_ids,
        "auto_select_must_dos": st.session_state.auto_select_must_dos,
        "itinerary_plan": st.session_state.itinerary_plan,
        "rejected_activities": st.session_state.rejected_activities,
        "itinerary_narrative": st.session_state.itinerary_narrative,
    }
    access_role = str(st.session_state.get("saved_trip_access_role") or "owner")
    if access_role == "collaborator":
        if not save_itinerary_version:
            raise ValueError("Collaborators can save itinerary versions only")
        access_token = str(account.get("access_token") or "")
        owner_id = str(st.session_state.get("saved_trip_owner_id") or "")
        trip_id = str(st.session_state.get("saved_trip_id") or "")
        shared_record = next(
            (
                record
                for record in list_saved_trips(
                    st.session_state.feedback_session_id,
                    auth_access_token=access_token,
                )
                if record.owner_id == owner_id
                and record.trip_id == trip_id
                and record.access_role == "collaborator"
            ),
            None,
        )
        if shared_record is None:
            raise RuntimeError("This collaborator trip is no longer available")
        saved = save_shared_itinerary_version(
            shared_record,
            planning_state,
            access_token,
        )
    else:
        saved = save_trip(
            trip,
            planning_state,
            trip_id=st.session_state.saved_trip_id,
            session_id=st.session_state.feedback_session_id,
            save_itinerary_version=save_itinerary_version,
            auth_access_token=account.get("access_token"),
            owner_id=st.session_state.get("saved_trip_owner_id"),
        )
    st.session_state.saved_trip_id = saved.trip_id
    st.session_state.saved_trip_owner_id = getattr(
        saved,
        "owner_id",
        st.session_state.feedback_session_id,
    )
    st.session_state.saved_trip_access_role = getattr(
        saved,
        "access_role",
        access_role,
    )
    st.session_state[SAVED_TRIP_CONFIRMATION_KEY] = {
        "trip_id": saved.trip_id,
        "title": saved.title,
        "account_backed": bool(account.get("access_token")),
        "collaborator": access_role == "collaborator",
    }
    return saved


def _open_my_trips() -> None:
    st.session_state.app_workspace = "My trips"


def _dismiss_saved_trip_confirmation() -> None:
    st.session_state.pop(SAVED_TRIP_CONFIRMATION_KEY, None)


def _render_saved_trip_confirmation() -> None:
    """Keep a successful save visible until the traveler acknowledges it."""

    confirmation = st.session_state.get(SAVED_TRIP_CONFIRMATION_KEY)
    if not isinstance(confirmation, dict):
        return
    title = str(confirmation.get("title") or "Trip")
    if confirmation.get("account_backed"):
        st.success(
            (
                f"A new itinerary version is saved to {title}."
                if confirmation.get("collaborator")
                else f"{title} is saved to your account."
            ),
            icon=":material/cloud_done:",
        )
    else:
        st.success(
            f"{title} is saved for this active session.",
            icon=":material/bookmark_added:",
        )
        st.caption("Sign in to keep it available across devices and new sessions.")
    with st.container(horizontal=True):
        st.button(
            "View in My trips",
            icon=":material/folder_open:",
            type="primary",
            key="view-recently-saved-trip",
            on_click=_open_my_trips,
        )
        st.button(
            "Dismiss",
            icon=":material/close:",
            key="dismiss-recently-saved-trip",
            on_click=_dismiss_saved_trip_confirmation,
        )


def _render_itinerary(
    trip: TripRequest,
    plan: ItineraryPlan,
    activity_by_id: dict[str, Activity],
    candidate_activities: list[Activity],
    ranked_results: list[GroupFitResult],
    owners_by_activity_id: dict[str, tuple[str, ...]],
    *,
    read_only: bool = False,
) -> None:
    scheduled = [
        activity
        for day in plan.days
        for activity in day.activities
    ]
    total_activity_hours = sum(day.activity_hours for day in plan.days)
    traveler_names = {
        traveler_name
        for activity in scheduled
        for traveler_name in activity.traveler_names
    }
    narrative = _load_grounded_narrative(plan)
    narrative_by_activity_id = (
        {
            activity.activity_id: activity
            for day in narrative.days
            for activity in day.activities
        }
        if narrative is not None
        else {}
    )

    with st.container(key="itinerary-shell"):
        st.markdown(
            '<div class="ts-section-label">Saved itinerary</div>'
            if read_only
            else '<div class="ts-section-label">Ready to explore</div>',
            unsafe_allow_html=True,
        )
        st.header(f"Your {len(plan.days)}-day itinerary")
        if not read_only:
            save_label = (
                "Save another itinerary version"
                if st.session_state.saved_trip_id
                else "Save trip & itinerary"
            )
            if st.button(
                save_label,
                icon=":material/bookmark_add:",
                key="save-itinerary",
                type="primary",
            ):
                try:
                    saved = _save_current_trip(
                        trip,
                        save_itinerary_version=True,
                    )
                except (RuntimeError, ValueError):
                    st.error(
                        "TripSync could not save this itinerary version. Your "
                        "collaboration access may have changed; return to My trips "
                        "and try again.",
                        icon=":material/error:",
                    )
                else:
                    st.toast(f"Saved {saved.title}")
            _render_saved_trip_confirmation()
        summary_labels = [
            f"{len(scheduled)} activities",
            f"{format_duration(total_activity_hours)} of activities",
            f"{plan.pace.value} pace",
        ]
        if traveler_names:
            summary_labels.append(
                f"{len(traveler_names)} travelers represented"
            )
        st.markdown(_chip_row(summary_labels), unsafe_allow_html=True)
        st.caption(
            "Daily timing includes a 30-minute planning buffer between activities. "
            "When catalog coordinates are available, stops are ordered to reduce "
            "backtracking and show a walking estimate below."
        )
        if not read_only and st.session_state.itinerary_notice:
            st.info(
                st.session_state.itinerary_notice,
                icon=":material/swap_horiz:",
            )
        if not read_only and st.session_state.itinerary_undo:
            if st.button(
                "Undo last replacement",
                key="undo-itinerary-replacement",
                icon=":material/undo:",
                type="secondary",
            ):
                _undo_last_replacement()
                st.toast("The previous itinerary is back.")
                st.rerun()

        if not read_only:
            narrative = _render_narration(trip, plan, activity_by_id)
        if narrative is not None:
            narrative_by_activity_id = {
                activity.activity_id: activity
                for day in narrative.days
                for activity in day.activities
            }

        for day in plan.days:
            with st.container(
                key=f"itinerary-day-{day.day_number}",
                border=True,
            ):
                title_col, timing_col = st.columns(
                    [3, 2],
                    vertical_alignment="center",
                )
                title_col.subheader(f"Day {day.day_number}")
                timing_col.caption(
                    f"{day.planned_hours:g} of "
                    f"{day.capacity_hours:g} hours planned"
                )
                if day.pace_override_approved:
                    timing_col.badge(
                        "Over recommended pace",
                        icon=":material/schedule:",
                        color="orange",
                    )
                    timing_col.caption("Added with organizer approval.")

                if not day.activities:
                    st.caption(
                        "Keep this day open for rest, wandering, or something "
                        "spontaneous."
                    )
                    continue

                route_summary = route_summary_for_day(day, activity_by_id)
                if route_summary.status == "spread_out":
                    st.warning(
                        route_summary.message,
                        icon=":material/directions_walk:",
                    )
                else:
                    st.caption(route_summary.message)
                route_legs_by_destination = {
                    leg.to_activity_id: leg for leg in route_summary.legs
                }

                for position, scheduled_activity in enumerate(day.activities):
                    activity = activity_by_id.get(
                        scheduled_activity.activity_id
                    )
                    icon = (
                        CATEGORY_ICONS.get(activity.category, "🧭")
                        if activity is not None
                        else "🧭"
                    )
                    slot = _itinerary_slot_label(
                        position,
                        len(day.activities),
                    )
                    st.markdown(
                        f"**{slot} · {icon} "
                        f"{scheduled_activity.activity_name}**"
                    )
                    source_label = (
                        "Your shortlist"
                        if scheduled_activity.source
                        == ItinerarySource.SHORTLIST
                        else "Group recommendation"
                    )
                    details = [
                        format_duration(scheduled_activity.duration_hours),
                        source_label,
                    ]
                    if scheduled_activity.traveler_names:
                        details.append(
                            "Serves "
                            + " + ".join(
                                scheduled_activity.traveler_names
                            )
                        )
                    st.caption(" · ".join(details))
                    st.caption(scheduled_activity.reason)
                    leg = route_legs_by_destination.get(
                        scheduled_activity.activity_id
                    )
                    if leg is not None:
                        st.caption(
                            "From the previous stop: about "
                            f"{leg.distance_km:g} km · {leg.walking_minutes} min walk"
                        )
                    activity_story = narrative_by_activity_id.get(
                        scheduled_activity.activity_id
                    )
                    if activity_story is not None:
                        st.markdown(
                            f"**Why it fits:** {activity_story.why_it_fits}"
                        )
                        if activity_story.practical_note:
                            st.caption(activity_story.practical_note)
                    if activity is not None and not read_only:
                        with st.popover(
                            "Replace activity",
                            key=(
                                "replace-activity-"
                                f"{scheduled_activity.activity_id}"
                            ),
                            icon=":material/swap_horiz:",
                            type="tertiary",
                        ):
                            if scheduled_activity.must_do_owners:
                                st.warning(
                                    "This is a must-do for "
                                    + " + ".join(
                                        scheduled_activity.must_do_owners
                                    )
                                    + ". Replacing it will count as an "
                                    "intentional override.",
                                    icon=":material/bookmark_remove:",
                                )
                            reason_value = st.selectbox(
                                "Why replace this activity?",
                                [
                                    reason.value
                                    for reason in RejectionReason
                                ],
                                key=(
                                    "rejection-reason-"
                                    f"{scheduled_activity.activity_id}"
                                ),
                            )
                            note = ""
                            if reason_value == RejectionReason.OTHER.value:
                                note = st.text_input(
                                    "Optional note",
                                    key=(
                                        "rejection-note-"
                                        f"{scheduled_activity.activity_id}"
                                    ),
                                    max_chars=300,
                                )
                            if st.button(
                                "Replace on this day",
                                key=(
                                    "confirm-reject-"
                                    f"{scheduled_activity.activity_id}"
                                ),
                                icon=":material/find_replace:",
                                type="primary",
                                width="stretch",
                            ):
                                _reject_and_replace_activity(
                                    plan,
                                    activity,
                                    RejectionReason(reason_value),
                                    note,
                                    candidate_activities,
                                    ranked_results,
                                    owners_by_activity_id,
                                )
                                st.toast(
                                    f"Rejected {activity.name} and updated "
                                    f"Day {day.day_number}."
                                )
                                st.rerun()

        if not read_only:
            _render_change_proposals(
                trip,
                plan,
                candidate_activities,
                ranked_results,
                owners_by_activity_id,
            )

        if plan.unscheduled:
            st.warning(
                f"{len(plan.unscheduled)} shortlisted "
                f"{'activity could' if len(plan.unscheduled) == 1 else 'activities could'} "
                "not fit within the selected pace.",
                icon=":material/event_busy:",
            )
            for item in plan.unscheduled:
                st.markdown(f"**{item.activity_name}** — {item.reason}")

        if not read_only:
            _render_overall_experience_feedback(trip, plan)


def _open_saved_trip_editor() -> None:
    """Return a saved itinerary to the normal matches-and-editing workspace."""

    st.session_state.saved_trip_read_mode = False
    st.session_state.planner_step = "results"
    st.rerun()


def _render_saved_trip_reader(
    trip: TripRequest,
    activity_by_id: dict[str, Activity],
) -> None:
    """Show a focused saved itinerary without re-running recommendation work."""

    st.caption(
        "Read this saved plan first. Edit matched attractions only when you "
        "want to compare or change the itinerary."
    )
    if st.button(
        "Edit matched attractions",
        icon=":material/edit_calendar:",
        type="primary",
        key="edit-saved-trip-matches",
    ):
        _open_saved_trip_editor()
    _render_summary(trip, show_edit=False)
    plan_payload = st.session_state.itinerary_plan
    if not plan_payload:
        st.info(
            "This trip was saved before an itinerary was built. Choose Edit "
            "matched attractions to build one.",
            icon=":material/calendar_month:",
        )
        return
    _render_itinerary(
        trip,
        ItineraryPlan.model_validate(plan_payload),
        activity_by_id,
        [],
        [],
        {},
        read_only=True,
    )


def _render_shortlist(
    trip: TripRequest,
    candidate_activities: list[Activity],
    ranked_results: list[GroupFitResult],
    activity_by_id: dict[str, Activity],
    owners_by_activity_id: dict[str, tuple[str, ...]],
) -> None:
    selected_ids = [
        activity_id
        for activity_id in st.session_state.selected_activity_ids
        if activity_id in activity_by_id
    ]
    st.session_state.selected_activity_ids = selected_ids

    with st.container(key="shortlist-card"):
        st.markdown(
            '<div class="ts-section-label">Saved for this trip</div>',
            unsafe_allow_html=True,
        )
        st.header("Your trip shortlist")
        if not selected_ids:
            st.caption(
                "Add activities from any view. Your choices stay here while "
                "you compare the results."
            )
        else:
            total_duration = sum(
                activity_by_id[activity_id].duration_hours
                for activity_id in selected_ids
            )
            st.markdown(
                _chip_row(
                    [
                        f"{len(selected_ids)} activities",
                        f"{format_duration(total_duration)} total",
                    ]
                ),
                unsafe_allow_html=True,
            )

            for activity_id in selected_ids:
                activity = activity_by_id[activity_id]
                owners = owners_by_activity_id.get(activity_id, ())
                detail_col, action_col = st.columns(
                    [4, 1],
                    vertical_alignment="center",
                )
                icon = CATEGORY_ICONS.get(activity.category, "🧭")
                detail_col.markdown(f"**{icon} {activity.name}**")
                if owners:
                    detail = f"Must-do for {_owner_names(owners)}"
                else:
                    detail = "Added from group recommendations"
                detail_col.markdown(
                    '<div class="ts-shortlist-meta">'
                    f"{escape(detail)} · "
                    f"{format_duration(activity.duration_hours)}"
                    "</div>",
                    unsafe_allow_html=True,
                )
                if action_col.button(
                    "Remove",
                    key=f"shortlist-remove-{activity_id}",
                    icon=":material/close:",
                    type="tertiary",
                    width="stretch",
                ):
                    _remove_activity_from_shortlist(
                        activity_id,
                        is_must_do=bool(owners),
                    )
                    st.toast(f"Removed {activity.name} from your trip.")

        st.space("small")
        auto_fill = st.toggle(
            "Fill open time with group recommendations",
            value=True,
            key="itinerary_auto_fill",
            help=(
                "TripSync keeps your shortlist first, then uses the existing "
                "group-fit ranking to fill remaining daily capacity."
            ),
            on_change=_invalidate_itinerary,
        )
        st.caption(
            "The planner respects your trip length and pace. Must-dos are "
            "prioritized only while they remain in this shortlist."
        )
        can_build = bool(candidate_activities) and bool(
            selected_ids or auto_fill
        )
        button_label = (
            "Rebuild our itinerary"
            if st.session_state.itinerary_plan
            else "Build our itinerary"
        )
        if st.button(
            button_label,
            key="build-itinerary",
            icon=":material/calendar_month:",
            type="primary",
            width="stretch",
            disabled=not can_build,
        ):
            plan = build_itinerary(
                trip,
                candidate_activities,
                ranked_results,
                selected_ids,
                must_do_owners_by_activity_id=owners_by_activity_id,
                excluded_activity_ids=_excluded_activity_ids(),
                auto_fill=auto_fill,
            )
            st.session_state.itinerary_plan = plan.model_dump(mode="json")
            st.session_state.itinerary_undo = None
            st.session_state.itinerary_notice = None
            st.session_state.itinerary_narrative = None
            st.session_state.itinerary_narration_error = None
            st.toast("Your itinerary is ready.")

    if st.session_state.itinerary_plan:
        _render_itinerary(
            trip,
            ItineraryPlan.model_validate(
                st.session_state.itinerary_plan
            ),
            activity_by_id,
            candidate_activities,
            ranked_results,
            owners_by_activity_id,
        )


def _render_results_step() -> None:
    if not st.session_state.trip_request:
        st.session_state.planner_step = "trip"
        st.rerun()

    trip = TripRequest.model_validate(st.session_state.trip_request)
    activities = load_activity_catalog()
    activity_by_id = _activity_lookup(activities)
    if st.session_state.saved_trip_read_mode:
        _render_saved_trip_reader(trip, activity_by_id)
        return
    has_itinerary = bool(st.session_state.itinerary_plan)
    can_save_trip_brief = (
        st.session_state.get("saved_trip_access_role") != "collaborator"
    )
    if not has_itinerary and can_save_trip_brief and st.button(
        "Save this trip",
        icon=":material/bookmark_add:",
        key="save-trip",
    ):
        _save_current_trip(
            trip,
            save_itinerary_version=False,
        )
        st.toast("Saved trip preferences")
    elif not has_itinerary and not can_save_trip_brief:
        st.caption(
            "Build an itinerary to add a new version to this shared trip. Only "
            "the owner can change or resave the trip brief."
        )
    if not has_itinerary:
        _render_saved_trip_confirmation()
    retrieval_response = _retrieve_for_current_trip(trip, activities)
    retrieval_by_activity_id = {
        result.activity_id: result
        for result in retrieval_response.results
    }
    retrieved_activities = [
        activity_by_id[result.activity_id]
        for result in retrieval_response.results
    ]
    destination_activities = [
        activity_by_id[activity_id]
        for activity_id in retrieval_response.destination_activity_ids
    ]
    results = rank_activities(
        retrieved_activities,
        trip,
        semantic_similarities_by_activity={
            retrieved.activity_id: dict(retrieved.semantic_similarities)
            for retrieved in retrieval_response.results
        },
    )
    result_by_activity_id = {
        result.activity_id: result for result in results
    }
    must_do_resolution = resolve_must_dos(
        destination_activities,
        trip.travelers,
    )
    owners_by_activity_id = must_do_resolution.owners_by_activity_id
    _sync_initial_must_dos(list(owners_by_activity_id))
    rejected_records = _rejected_activity_records()
    rejected_ids = set(rejected_records)
    active_results = [
        result
        for result in results
        if result.activity_id not in rejected_ids
    ]
    active_must_do_ids = [
        activity_id
        for activity_id in owners_by_activity_id
        if activity_id not in rejected_ids
    ]

    _render_summary(trip)
    st.space("medium")
    st.markdown('<div class="ts-section-label">Balanced for your group</div>', unsafe_allow_html=True)
    st.header("Your strongest matches")
    st.markdown(
        '<p class="ts-helper">Scores combine shared interests, walking comfort, '
        "must-dos, budget, and fairness. Switch views without losing anything "
        "you add to your trip.</p>",
        unsafe_allow_html=True,
    )

    if not retrieval_response.destination_activity_ids:
        available_destinations = catalog_destination_options(activities)
        st.warning(
            "There are no curated activities for "
            f"{trip.destination}, {trip.country} yet.",
            icon=":material/location_off:",
        )
        if available_destinations:
            destination_labels = list(available_destinations)
            visible_destinations = destination_labels[:6]
            remaining_count = len(destination_labels) - len(visible_destinations)
            availability_text = ", ".join(visible_destinations)
            if remaining_count:
                availability_text += (
                    f", and {remaining_count} more in the destination picker"
                )
            st.caption(
                f"Available now: {availability_text}."
            )
        if st.button(
            "Change destination",
            icon=":material/arrow_back:",
            type="primary",
            key="change-unsupported-destination",
        ):
            st.session_state.trip_basics = trip.model_dump(
                mode="json",
                exclude={"travelers"},
            )
            st.session_state.planner_step = "trip"
            st.rerun()
        return

    retrieval_label = (
        "Text retrieval fallback"
        if retrieval_response.semantic_fallback
        else (
            "Hosted semantic retrieval"
            if RETRIEVAL_MODE == "vector"
            else "Hybrid retrieval"
        )
    )
    st.caption(
        f"{retrieval_label} found {len(retrieval_response.results)} relevant "
        f"activities from {len(retrieval_response.destination_activity_ids)} "
        "destination records."
    )
    if retrieval_response.used_fallback:
        st.info(
            "No direct interest or must-do text matched, so TripSync is "
            "showing the complete destination catalog.",
            icon=":material/travel_explore:",
        )
    if retrieval_response.semantic_fallback:
        st.info(
            "Semantic retrieval is unavailable on this instance, so these "
            "results are using deterministic text matching.",
            icon=":material/offline_bolt:",
        )

    for unmatched in must_do_resolution.unmatched:
        _render_unmatched_must_do(unmatched)

    result_view = st.segmented_control(
        "Results to show",
        ["top_five", "must_dos", "all", "rejected"],
        default="top_five",
        format_func=lambda value: {
            "top_five": "Top 5",
            "must_dos": f"Must-dos ({len(active_must_do_ids)})",
            "all": "All activities",
            "rejected": f"Rejected ({len(rejected_records)})",
        }[value],
        label_visibility="collapsed",
        key="results_view",
    )

    if result_view == "must_dos":
        displayed_results = [
            (None, result_by_activity_id[activity_id])
            for activity_id in active_must_do_ids
        ]
    elif result_view == "all":
        displayed_results = list(enumerate(active_results, start=1))
    elif result_view == "rejected":
        displayed_results = [
            (None, result_by_activity_id[activity_id])
            for activity_id in rejected_records
            if activity_id in result_by_activity_id
        ]
    else:
        displayed_results = list(enumerate(active_results[:5], start=1))

    if not displayed_results:
        if result_view == "rejected":
            st.info(
                "No rejected activities yet. Anything you replace from an "
                "itinerary will stay available here.",
                icon=":material/history:",
            )
        elif result_view == "must_dos":
            st.info(
                "No active recognized must-dos yet. Edit the traveler "
                "profiles or restore one from Rejected.",
                icon=":material/bookmark:",
            )

    for rank, result in displayed_results:
        _render_result_card(
            rank,
            result,
            activity_by_id[result.activity_id],
            retrieval_by_activity_id[result.activity_id],
            owners_by_activity_id.get(result.activity_id, ()),
            rejected_records.get(result.activity_id),
        )

    _render_shortlist(
        trip,
        retrieved_activities,
        results,
        activity_by_id,
        owners_by_activity_id,
    )


def render_app() -> None:
    """Render the complete TripSync preference flow."""

    _initialize_state()
    _render_hero()
    _render_progress()

    step = st.session_state.planner_step
    if step == "trip":
        _render_trip_step()
    elif step == "travelers":
        _render_travelers_step()
    else:
        _render_results_step()
