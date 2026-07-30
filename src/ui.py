"""Streamlit preference flow and presentation helpers."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

import streamlit as st
from pydantic import ValidationError

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
from src.planner import build_itinerary, replace_itinerary_activity
from src.scoring import GroupFitResult, rank_activities
from src.search import RetrievedActivity, retrieve_activities


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DATA_PATH = REPOSITORY_ROOT / "data" / "sample_activities.json"

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


def format_duration(hours: float) -> str:
    """Format activity duration with correct singular/plural grammar."""

    label = f"{hours:g}"
    unit = "hour" if hours == 1 else "hours"
    return f"{label} {unit}"


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


@st.cache_data
def load_sample_activities() -> list[Activity]:
    """Load and validate the development activity catalog."""

    raw_activities = json.loads(SAMPLE_DATA_PATH.read_text(encoding="utf-8"))
    return [Activity.model_validate(activity) for activity in raw_activities]


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
        "itinerary_plan": None,
        "rejected_activities": {},
        "itinerary_undo": None,
        "itinerary_notice": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _reset_activity_selections() -> None:
    """Clear shortlist state when a new trip request is submitted."""

    st.session_state.selected_activity_ids = []
    st.session_state.dismissed_must_do_ids = []
    st.session_state.itinerary_plan = None
    st.session_state.rejected_activities = {}
    st.session_state.itinerary_undo = None
    st.session_state.itinerary_notice = None


def _invalidate_itinerary() -> None:
    """Discard a generated plan after its inputs change."""

    st.session_state.itinerary_plan = None
    st.session_state.itinerary_undo = None
    st.session_state.itinerary_notice = None


def _apply_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            --ocean-blue: #176BFF;
            --sunset-coral: #FF5A5F;
            --mango-yellow: #FFBE3D;
            --lagoon-teal: #00A99D;
            --deep-teal: #007F73;
            --deep-navy: #15324A;
            --warm-cream: #FFF8F0;
        }

        [data-testid="stAppViewContainer"] {
            background:
                radial-gradient(circle at 92% 7%, rgba(255, 190, 61, 0.28), transparent 24rem),
                radial-gradient(circle at 5% 34%, rgba(0, 169, 157, 0.15), transparent 28rem),
                linear-gradient(180deg, #EAF3FF 0rem, var(--warm-cream) 34rem);
        }

        [data-testid="stHeader"] {
            background: transparent;
        }

        .block-container {
            max-width: 1120px;
            padding-top: 1.5rem;
            padding-bottom: 5rem;
        }

        .st-key-hero {
            background: linear-gradient(125deg, #176BFF 0%, #0C86EA 52%, #00A99D 100%);
            border: 1px solid rgba(255,255,255,0.35);
            border-radius: 1.75rem;
            box-shadow: 0 24px 64px rgba(23, 107, 255, 0.24);
            color: white;
            overflow: hidden;
            padding: 1.9rem 2rem 1.6rem;
            position: relative;
            margin-bottom: 1.1rem;
        }

        .st-key-hero::before {
            background: var(--mango-yellow);
            border-radius: 50%;
            content: "";
            height: 9rem;
            opacity: 0.96;
            position: absolute;
            right: -2.2rem;
            top: -3.4rem;
            width: 9rem;
        }

        .st-key-hero::after {
            background:
                radial-gradient(circle, rgba(255,255,255,0.72) 1.5px, transparent 1.7px);
            background-size: 13px 13px;
            bottom: -2.2rem;
            content: "";
            height: 8rem;
            opacity: 0.34;
            position: absolute;
            right: 2.8rem;
            transform: rotate(-8deg);
            width: 13rem;
        }

        .st-key-hero > div {
            position: relative;
            z-index: 1;
        }

        .st-key-hero h1 {
            color: #FFFFFF;
            font-size: clamp(2.4rem, 6vw, 4.6rem);
            font-weight: 850;
            letter-spacing: -0.055em;
            line-height: 0.98;
            max-width: 48rem;
        }

        .ts-brand {
            color: var(--mango-yellow);
            font-size: 0.78rem;
            font-weight: 850;
            letter-spacing: 0.16em;
            text-transform: uppercase;
            margin-bottom: 0.35rem;
        }

        .ts-kicker {
            color: rgba(255,255,255,0.88);
            font-size: 1.04rem;
            line-height: 1.6;
            max-width: 44rem;
            margin-top: -0.35rem;
        }

        .ts-hero-badges {
            display: flex;
            flex-wrap: wrap;
            gap: 0.5rem;
            margin-top: 0.9rem;
        }

        .ts-hero-badge {
            align-items: center;
            backdrop-filter: blur(8px);
            background: rgba(255,255,255,0.17);
            border: 1px solid rgba(255,255,255,0.30);
            border-radius: 999px;
            color: #FFFFFF;
            display: inline-flex;
            font-size: 0.75rem;
            font-weight: 750;
            padding: 0.38rem 0.7rem;
        }

        .st-key-progress-card {
            background: linear-gradient(90deg, rgba(255,255,255,0.94), rgba(255,243,211,0.94));
            border: 1px solid rgba(23, 107, 255, 0.13);
            border-radius: 1rem;
            box-shadow: 0 8px 25px rgba(21, 50, 74, 0.06);
            padding: 0.35rem 0.8rem 0.15rem;
            margin-bottom: 1.2rem;
        }

        .st-key-trip-card,
        .st-key-travelers-shell,
        .st-key-summary-card,
        .st-key-shortlist-card {
            background: rgba(255,255,255,0.94);
            border: 1px solid rgba(23, 107, 255, 0.14);
            border-radius: 1.35rem;
            box-shadow: 0 16px 40px rgba(21, 50, 74, 0.075);
            padding: 1.35rem 1.45rem 1.1rem;
        }

        .st-key-summary-card {
            background: linear-gradient(115deg, #FFFFFF 0%, #FFF1C8 100%);
            border-color: rgba(255, 190, 61, 0.42);
        }

        .st-key-shortlist-card {
            background: linear-gradient(125deg, #FFFFFF 0%, #E9FBF9 100%);
            border-color: rgba(0, 169, 157, 0.28);
            margin-top: 1.5rem;
        }

        .st-key-itinerary-shell {
            margin-top: 2.25rem;
        }

        div[class*="st-key-itinerary-day-"] {
            background: rgba(255,255,255,0.94);
            border: 1px solid rgba(23, 107, 255, 0.18);
            border-left: 5px solid var(--ocean-blue);
            border-radius: 1.15rem;
            box-shadow: 0 14px 32px rgba(21, 50, 74, 0.08);
            margin: 0.8rem 0;
            padding: 0.45rem 0.85rem;
        }

        div[class*="st-key-traveler-card-"] {
            background: linear-gradient(150deg, #FFFFFF, #EDF5FF);
            border: 1px solid rgba(23, 107, 255, 0.18);
            border-top: 5px solid var(--ocean-blue);
            border-radius: 1.1rem;
            padding: 1rem 1.05rem 0.85rem;
        }

        .st-key-traveler-card-2,
        .st-key-traveler-card-5 {
            background: linear-gradient(150deg, #FFFFFF, #FFF0F0) !important;
            border-color: rgba(255, 90, 95, 0.20) !important;
            border-top-color: var(--sunset-coral) !important;
        }

        .st-key-traveler-card-3,
        .st-key-traveler-card-6 {
            background: linear-gradient(150deg, #FFFFFF, #E9FBF9) !important;
            border-color: rgba(0, 169, 157, 0.20) !important;
            border-top-color: var(--lagoon-teal) !important;
        }

        .st-key-traveler-card-4 {
            background: linear-gradient(150deg, #FFFFFF, #FFF5D9) !important;
            border-color: rgba(255, 190, 61, 0.28) !important;
            border-top-color: var(--mango-yellow) !important;
        }

        div[class*="st-key-result-card-"] {
            background: rgba(255,255,255,0.96);
            border: 1px solid rgba(23, 107, 255, 0.13);
            border-top: 5px solid var(--ocean-blue);
            border-radius: 1.25rem;
            box-shadow: 0 12px 30px rgba(21, 50, 74, 0.07);
            padding: 1.15rem 1.25rem 0.85rem;
            margin-bottom: 0.85rem;
        }

        div[class*="st-key-result-card-1-"] {
            border-top-color: var(--sunset-coral);
            box-shadow: 0 16px 42px rgba(255, 90, 95, 0.13);
        }

        div[class*="st-key-result-card-2-"] {
            border-top-color: var(--ocean-blue);
        }

        div[class*="st-key-result-card-3-"] {
            border-top-color: var(--lagoon-teal);
        }

        div[class*="st-key-result-card-"]:hover {
            box-shadow: 0 18px 45px rgba(23, 107, 255, 0.14);
            transform: translateY(-2px);
            transition: all 160ms ease;
        }

        .ts-section-label {
            color: #D8444A;
            font-size: 0.76rem;
            font-weight: 850;
            letter-spacing: 0.14em;
            text-transform: uppercase;
        }

        .ts-helper {
            color: rgba(21,50,74,0.68);
            line-height: 1.55;
            margin-bottom: 1rem;
        }

        .ts-chip-row {
            display: flex;
            flex-wrap: wrap;
            gap: 0.45rem;
            margin: 0.3rem 0 0.8rem;
        }

        .ts-chip {
            background: rgba(0,169,157,0.11);
            border: 1px solid rgba(0,127,115,0.16);
            border-radius: 999px;
            color: var(--deep-teal);
            display: inline-block;
            font-size: 0.76rem;
            font-weight: 750;
            padding: 0.28rem 0.58rem;
        }

        .ts-score {
            align-items: baseline;
            color: var(--deep-teal);
            display: flex;
            gap: 0.15rem;
        }

        .ts-score strong {
            font-family: inherit;
            font-size: 2.3rem;
            font-weight: 850;
            letter-spacing: -0.05em;
            line-height: 1;
        }

        .ts-score span {
            color: rgba(21,50,74,0.56);
            font-size: 0.8rem;
            font-weight: 700;
        }

        .ts-tradeoff {
            background: rgba(255,190,61,0.17);
            border-left: 3px solid var(--sunset-coral);
            border-radius: 0.45rem;
            color: rgba(21,50,74,0.80);
            font-size: 0.82rem;
            margin: 0.35rem 0;
            padding: 0.45rem 0.65rem;
        }

        .ts-must-do-line {
            background: rgba(255,90,95,0.10);
            border-left: 3px solid var(--sunset-coral);
            border-radius: 0.45rem;
            color: #B5323A;
            font-size: 0.82rem;
            font-weight: 800;
            margin: 0.4rem 0 0.7rem;
            padding: 0.42rem 0.65rem;
        }

        .ts-rejected-line {
            background: rgba(111, 79, 166, 0.10);
            border-left: 3px solid #6F4FA6;
            border-radius: 0.45rem;
            color: #5B3E8A;
            font-size: 0.82rem;
            font-weight: 750;
            margin: 0.4rem 0 0.7rem;
            padding: 0.42rem 0.65rem;
        }

        .ts-shortlist-meta {
            color: rgba(21,50,74,0.62);
            font-size: 0.78rem;
            margin-top: -0.45rem;
        }

        div[data-testid="stForm"] {
            border: 0;
            padding: 0;
        }

        div[data-testid="stAlert"] {
            border-radius: 0.85rem;
        }

        @media (max-width: 700px) {
            .block-container {
                padding-left: 1rem;
                padding-right: 1rem;
            }

            .st-key-hero {
                padding: 1.15rem;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_hero() -> None:
    with st.container(key="hero"):
        st.markdown('<div class="ts-brand">TripSync · Everyone gets a say</div>', unsafe_allow_html=True)
        st.title("Your next great trip starts together.")
        st.markdown(
            '<p class="ts-kicker">Mix everyone’s interests, pace, budget, and '
            "must-dos. TripSync finds the bright spots your whole crew can get "
            "excited about—without hiding the trade-offs.</p>",
            unsafe_allow_html=True,
        )
        st.markdown(
            """
            <div class="ts-hero-badges" aria-label="TripSync planning benefits">
                <span class="ts-hero-badge">✦ Everyone heard</span>
                <span class="ts-hero-badge">☀ Fair matches</span>
                <span class="ts-hero-badge">↗ Trade-offs explained</span>
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
    with st.container(key="trip-card"):
        st.markdown('<div class="ts-section-label">Start with the shape of the trip</div>', unsafe_allow_html=True)
        st.header("Where are you going?")
        st.markdown(
            '<p class="ts-helper">A few shared boundaries make the individual '
            "preferences easier to balance later.</p>",
            unsafe_allow_html=True,
        )

        with st.form("trip_basics_form"):
            destination_col, country_col = st.columns(2, gap="large")
            destination = destination_col.text_input(
                "Destination city",
                value=basics["destination"],
                placeholder="Rome",
            )
            country = country_col.text_input(
                "Country",
                value=basics["country"],
                placeholder="Italy",
            )

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
        "interests": interests or [],
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


def _render_summary(trip: TripRequest) -> None:
    with st.container(key="summary-card"):
        heading_col, action_col = st.columns([4, 1], vertical_alignment="center")
        heading_col.markdown('<div class="ts-section-label">Your planning brief</div>', unsafe_allow_html=True)
        heading_col.subheader(
            f"{trip.destination}, {trip.country} · {trip.days} days"
        )
        if action_col.button(
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
) -> None:
    selected = activity.id in st.session_state.selected_activity_ids
    label = "Remove from trip" if selected else "Add to trip"
    icon = ":material/remove_circle:" if selected else ":material/add_circle:"
    if st.button(
        label,
        key=f"activity-selection-{activity.id}",
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

            with st.expander("Why this fits your group"):
                st.markdown("**Why it was retrieved**")
                for reason in retrieval.reasons:
                    st.caption(reason)

                st.markdown("**How it fits each traveler**")
                for fit in result.traveler_fits:
                    status = "✓" if fit.matched_interests or fit.must_do_match else "○"
                    st.markdown(
                        f"**{status} {fit.traveler_name} · {fit.score:.0f}/100**"
                    )
                    for explanation in fit.explanations:
                        st.caption(explanation)

                st.caption(activity.accessibility_notes)
                st.link_button(
                    "View activity source",
                    str(activity.source_url),
                    icon=":material/open_in_new:",
                )

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


def _render_itinerary(
    plan: ItineraryPlan,
    activity_by_id: dict[str, Activity],
    candidate_activities: list[Activity],
    ranked_results: list[GroupFitResult],
    owners_by_activity_id: dict[str, tuple[str, ...]],
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

    with st.container(key="itinerary-shell"):
        st.markdown(
            '<div class="ts-section-label">Ready to explore</div>',
            unsafe_allow_html=True,
        )
        st.header(f"Your {len(plan.days)}-day itinerary")
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
            "Daily timing includes a 30-minute transition estimate between "
            "activities. Opening hours and route optimization are not live yet."
        )
        if st.session_state.itinerary_notice:
            st.info(
                st.session_state.itinerary_notice,
                icon=":material/swap_horiz:",
            )
        if st.session_state.itinerary_undo:
            if st.button(
                "Undo last replacement",
                key="undo-itinerary-replacement",
                icon=":material/undo:",
                type="secondary",
            ):
                _undo_last_replacement()
                st.toast("The previous itinerary is back.")
                st.rerun()

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

                if not day.activities:
                    st.caption(
                        "Keep this day open for rest, wandering, or something "
                        "spontaneous."
                    )
                    continue

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
                    if activity is not None:
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

        if plan.unscheduled:
            st.warning(
                f"{len(plan.unscheduled)} shortlisted "
                f"{'activity could' if len(plan.unscheduled) == 1 else 'activities could'} "
                "not fit within the selected pace.",
                icon=":material/event_busy:",
            )
            for item in plan.unscheduled:
                st.markdown(f"**{item.activity_name}** — {item.reason}")


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
                    st.rerun()

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
            st.toast("Your itinerary is ready.")
            st.rerun()

    if st.session_state.itinerary_plan:
        _render_itinerary(
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
    activities = load_sample_activities()
    activity_by_id = _activity_lookup(activities)
    retrieval_response = retrieve_activities(activities, trip)
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
    results = rank_activities(retrieved_activities, trip)
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
        st.warning(
            "The current activity catalog has no entries for "
            f"{trip.destination}, {trip.country}. Try Rome, Italy while "
            "the prototype catalog expands.",
            icon=":material/location_off:",
        )
        _render_shortlist(
            trip,
            retrieved_activities,
            results,
            activity_by_id,
            owners_by_activity_id,
        )
        return

    st.caption(
        f"Text retrieval found {len(retrieval_response.results)} relevant "
        f"activities from {len(retrieval_response.destination_activity_ids)} "
        "destination records."
    )
    if retrieval_response.used_fallback:
        st.info(
            "No direct interest or must-do text matched, so TripSync is "
            "showing the complete destination catalog.",
            icon=":material/travel_explore:",
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
    _apply_styles()
    _render_hero()
    _render_progress()

    step = st.session_state.planner_step
    if step == "trip":
        _render_trip_step()
    elif step == "travelers":
        _render_travelers_step()
    else:
        _render_results_step()
