"""Streamlit preference flow and presentation helpers."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

import streamlit as st
from pydantic import ValidationError

from src.models import Activity, TravelerProfile, TripRequest
from src.scoring import GroupFitResult, rank_activities


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
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


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
        .st-key-summary-card {
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


def _render_result_card(
    rank: int,
    result: GroupFitResult,
    activity: Activity,
) -> None:
    with st.container(key=f"result-card-{rank}-{result.activity_id}"):
        score_col, content_col = st.columns([1, 4], gap="large")
        with score_col:
            st.caption(f"#{rank} group fit")
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


def _render_results_step() -> None:
    if not st.session_state.trip_request:
        st.session_state.planner_step = "trip"
        st.rerun()

    trip = TripRequest.model_validate(st.session_state.trip_request)
    activities = load_sample_activities()
    activity_by_id = _activity_lookup(activities)
    results = rank_activities(activities, trip)

    _render_summary(trip)
    st.space("medium")
    st.markdown('<div class="ts-section-label">Balanced for your group</div>', unsafe_allow_html=True)
    st.header("Your strongest matches")
    st.markdown(
        '<p class="ts-helper">Scores combine shared interests, walking comfort, '
        "must-dos, budget, and fairness. Open any card to see the reasoning.</p>",
        unsafe_allow_html=True,
    )

    result_count = st.segmented_control(
        "Results to show",
        [5, len(results)],
        default=5,
        format_func=lambda value: "Top 5" if value == 5 else "All activities",
        label_visibility="collapsed",
    )
    for rank, result in enumerate(results[: result_count or 5], start=1):
        _render_result_card(
            rank,
            result,
            activity_by_id[result.activity_id],
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
