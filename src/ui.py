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

from src.catalog import load_curated_activities
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
from src.trips import save_trip


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


@st.cache_data(ttl="2m", max_entries=1)
def load_activity_catalog() -> list[Activity]:
    """Load the canonical catalog without querying Supabase on every click."""

    return load_curated_activities()


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
        "itinerary_narrative": None,
        "itinerary_narration_error": None,
        "itinerary_change_proposals": None,
        "itinerary_change_error": None,
        "retrieval_cache": None,
        "feedback_session_id": uuid4().hex,
        "saved_trip_id": None,
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
    st.session_state.itinerary_narrative = None
    st.session_state.itinerary_narration_error = None
    st.session_state.itinerary_change_proposals = None
    st.session_state.itinerary_change_error = None
    st.session_state.retrieval_cache = None


def _invalidate_itinerary() -> None:
    """Discard a generated plan after its inputs change."""

    st.session_state.itinerary_plan = None
    st.session_state.itinerary_undo = None
    st.session_state.itinerary_notice = None
    st.session_state.itinerary_narrative = None
    st.session_state.itinerary_narration_error = None
    st.session_state.itinerary_change_proposals = None
    st.session_state.itinerary_change_error = None


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


def _apply_styles(workspace: str = "Plan a trip") -> None:
    workspace_colors = {
        "Plan a trip": {
            "accent": "#D7897F",
            "primary": "#8C4640",
            "soft": "#F8E4DF",
            "glow": "rgba(215,137,127,0.24)",
        },
        "My trips": {
            "accent": "#F9B95C",
            "primary": "#8A5E17",
            "soft": "#FDEDD2",
            "glow": "rgba(249,185,92,0.24)",
        },
        "Feedback insights": {
            "accent": "#96C7B3",
            "primary": "#2F5A49",
            "soft": "#E4F1EC",
            "glow": "rgba(150,199,179,0.28)",
        },
        "Curate catalog": {
            "accent": "#6398A9",
            "primary": "#1F4552",
            "soft": "#E4EFF2",
            "glow": "rgba(99,152,169,0.25)",
        },
    }
    page_colors = workspace_colors.get(
        workspace,
        workspace_colors["Plan a trip"],
    )
    st.markdown(
        f"""
        <style>
        :root {{
            --page-accent: {page_colors["accent"]};
            --page-primary: {page_colors["primary"]};
            --page-soft: {page_colors["soft"]};
            --page-glow: {page_colors["glow"]};
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        """
        <style>
        :root {
            --nectarine: #D7897F;
            --nectarine-dark: #8C4640;
            --peach: #F9B95C;
            --peach-dark: #8A5E17;
            --mint: #96C7B3;
            --mint-dark: #2F5A49;
            --lagoon: #6398A9;
            --lagoon-dark: #1F4552;
            --cream: #FDF6EF;
            --ink: #3A2E2B;
            --muted: #8A7A75;
            --paper: #FFFFFF;
        }

        [data-testid="stAppViewContainer"] {
            background:
                radial-gradient(circle at 8% 14%, var(--page-glow), transparent 22rem),
                var(--cream);
            transition: background 220ms ease;
        }

        [data-testid="stHeader"] {
            background: transparent;
        }

        .block-container {
            max-width: 1180px;
            padding-top: 1rem;
            padding-bottom: 5.5rem;
        }

        .st-key-workspace-nav {
            border-bottom: 1px solid var(--page-glow);
            isolation: isolate;
            margin-bottom: 1.7rem;
            padding: 0.35rem 0 0.9rem;
            position: relative;
            z-index: 20;
        }

        .ts-nav-brand {
            color: var(--nectarine-dark);
            font-family: "Baloo 2", sans-serif;
            font-size: 1.65rem;
            font-weight: 800;
            letter-spacing: -0.04em;
            line-height: 1;
        }

        .ts-nav-brand span {
            color: var(--lagoon);
        }

        .st-key-workspace-nav button {
            pointer-events: auto !important;
            position: relative;
            z-index: 21;
        }

        .st-key-workspace-nav button[data-testid="stBaseButton-tertiary"] {
            color: var(--ink);
        }

        .st-key-workspace-nav button[data-testid="stBaseButton-tertiary"]:hover {
            background: var(--page-soft);
        }

        .st-key-workspace-nav button[data-testid="stBaseButton-primary"],
        .st-key-workspace-page button[data-testid^="stBaseButton-primary"] {
            background: var(--page-primary);
            border-color: var(--page-primary);
            box-shadow: 0 8px 20px var(--page-glow);
        }

        .st-key-workspace-page button[data-testid="stBaseButton-secondary"] {
            background: rgba(255,255,255,0.72);
            border-color: var(--page-accent);
            color: var(--page-primary);
        }

        .st-key-workspace-page button[data-testid="stBaseButton-secondary"]:hover {
            background: var(--page-soft);
            border-color: var(--page-primary);
        }

        .st-key-workspace-page [data-testid="stSlider"]
        [data-rac][data-orientation="horizontal"] > div[data-rac]
        > div[data-rac] {
            background-color: var(--page-primary);
        }

        .st-key-workspace-plan-a-trip button[data-testid="stBaseButton-tertiary"] {
            color: var(--nectarine-dark);
        }

        .st-key-workspace-my-trips button[data-testid="stBaseButton-tertiary"] {
            color: var(--peach-dark);
        }

        .st-key-workspace-feedback-insights button[data-testid="stBaseButton-tertiary"] {
            color: var(--mint-dark);
        }

        .st-key-workspace-curate-catalog button[data-testid="stBaseButton-tertiary"] {
            color: var(--lagoon-dark);
        }

        .st-key-workspace-plan-a-trip button p::before,
        .st-key-workspace-my-trips button p::before,
        .st-key-workspace-feedback-insights button p::before,
        .st-key-workspace-curate-catalog button p::before {
            border-radius: 50%;
            content: "";
            display: inline-block;
            height: 0.48rem;
            margin-right: 0.42rem;
            vertical-align: 0.04rem;
            width: 0.48rem;
        }

        .st-key-workspace-plan-a-trip button p::before {
            background: var(--nectarine);
        }

        .st-key-workspace-my-trips button p::before {
            background: var(--peach);
        }

        .st-key-workspace-feedback-insights button p::before {
            background: var(--mint);
        }

        .st-key-workspace-curate-catalog button p::before {
            background: var(--lagoon);
        }

        .st-key-workspace-page {
            background: linear-gradient(
                180deg,
                var(--page-soft) 0,
                rgba(255,255,255,0.34) 25rem,
                rgba(255,255,255,0) 48rem
            );
            border-top: 6px solid var(--page-accent);
            border-radius: 1.8rem;
            box-shadow: 0 18px 48px var(--page-glow);
            padding: 1.45rem 1.5rem 2.25rem;
            transition: background 220ms ease, border-color 220ms ease;
        }

        .st-key-workspace-page h1,
        .st-key-workspace-page .ts-section-label,
        .st-key-workspace-page .ts-score {
            color: var(--page-primary);
        }

        .st-key-workspace-page div[data-testid="stVerticalBlockBorderWrapper"] {
            border-color: var(--page-glow);
        }

        .st-key-hero {
            margin-bottom: 1.4rem;
            padding: 0.8rem 0 1rem;
            position: relative;
        }

        .st-key-hero-copy {
            padding: 1.2rem 1.6rem 1rem 0.15rem;
        }

        .ts-brand {
            color: var(--nectarine);
            font-size: 0.76rem;
            font-weight: 800;
            letter-spacing: 0.14em;
            margin-bottom: 0.65rem;
            text-transform: uppercase;
        }

        .st-key-hero h1 {
            color: var(--nectarine-dark);
            font-family: "Baloo 2", sans-serif;
            font-size: clamp(2.8rem, 5.3vw, 4.5rem);
            font-weight: 800;
            letter-spacing: -0.045em;
            line-height: 0.98;
            margin: 0 0 1rem;
            max-width: 38rem;
        }

        .ts-kicker {
            color: var(--muted);
            font-size: 1.02rem;
            line-height: 1.65;
            margin: 0;
            max-width: 34rem;
        }

        .ts-hero-visual {
            background: linear-gradient(155deg, var(--peach) 0%, var(--nectarine) 100%);
            border-radius: 1.8rem;
            box-shadow: 0 20px 50px rgba(140,70,64,0.16);
            height: 22rem;
            overflow: hidden;
            position: relative;
        }

        .ts-hero-arch {
            background: var(--mint);
            border-radius: 10rem 10rem 0 0;
            bottom: 0;
            height: 82%;
            left: 50%;
            position: absolute;
            transform: translateX(-50%);
            width: 68%;
        }

        .ts-hero-route {
            border: 2px dashed rgba(47,90,73,0.40);
            border-bottom: 0;
            border-radius: 8rem 8rem 0 0;
            bottom: 0;
            height: 62%;
            left: 50%;
            position: absolute;
            transform: translateX(-50%);
            width: 44%;
        }

        .ts-hero-route::before,
        .ts-hero-route::after {
            background: var(--lagoon-dark);
            border: 4px solid rgba(255,255,255,0.92);
            border-radius: 50%;
            content: "";
            height: 1rem;
            position: absolute;
            width: 1rem;
        }

        .ts-hero-route::before {
            left: -0.55rem;
            top: 58%;
        }

        .ts-hero-route::after {
            right: -0.55rem;
            top: 10%;
        }

        .ts-visual-badge {
            background: rgba(255,255,255,0.96);
            border: 1px solid rgba(58,46,43,0.05);
            border-radius: 1rem;
            box-shadow: 0 8px 22px rgba(58,46,43,0.12);
            color: var(--lagoon-dark);
            font-size: 0.82rem;
            font-weight: 800;
            line-height: 1.25;
            padding: 0.72rem 0.9rem;
            position: absolute;
        }

        .ts-visual-badge small {
            color: var(--muted);
            display: block;
            font-size: 0.67rem;
            font-weight: 600;
            margin-top: 0.18rem;
        }

        .ts-visual-badge--top {
            left: 1.35rem;
            top: 1.35rem;
        }

        .ts-visual-badge--bottom {
            bottom: 1.35rem;
            right: 1.35rem;
        }

        .ts-hero-badges {
            display: flex;
            flex-wrap: wrap;
            gap: 0.55rem;
            margin-top: 1.35rem;
        }

        .ts-hero-badge {
            align-items: center;
            background: var(--paper);
            border: 1px solid #ECDDD3;
            border-radius: 999px;
            box-shadow: 0 5px 14px rgba(140,70,64,0.05);
            color: var(--nectarine-dark);
            display: inline-flex;
            font-size: 0.74rem;
            font-weight: 800;
            padding: 0.42rem 0.72rem;
        }

        .st-key-progress-card {
            background: rgba(255,255,255,0.78);
            border: 1px solid #EADBD2;
            border-radius: 1.1rem;
            box-shadow: 0 8px 22px rgba(140,70,64,0.05);
            margin-bottom: 1.15rem;
            padding: 0.35rem 0.9rem 0.12rem;
        }

        .st-key-trip-card,
        .st-key-travelers-shell,
        .st-key-summary-card,
        .st-key-shortlist-card {
            border-radius: 1.55rem;
            box-shadow: 0 14px 36px rgba(140,70,64,0.08);
            padding: 1.45rem 1.55rem 1.2rem;
        }

        .st-key-trip-card {
            background: var(--paper);
            border: 1px solid #EADBD2;
        }

        .st-key-travelers-shell {
            background: var(--paper);
            border: 1px solid rgba(47,90,73,0.16);
            color: #173D2E;
        }

        .st-key-summary-card {
            background: linear-gradient(120deg, #FFFDF9 0%, #FBE6C3 100%);
            border: 1px solid rgba(168,108,23,0.16);
        }

        .st-key-shortlist-card {
            background: linear-gradient(130deg, #FFFFFF 0%, #E8F1F3 100%);
            border: 1px solid rgba(99,152,169,0.30);
            margin-top: 1.5rem;
        }

        .st-key-itinerary-shell {
            margin-top: 2.25rem;
        }

        div[class*="st-key-itinerary-day-"] {
            background: rgba(255,255,255,0.96);
            border: 1px solid #EADBD2;
            border-left: 5px solid var(--lagoon);
            border-radius: 1.25rem;
            box-shadow: 0 10px 28px rgba(58,46,43,0.07);
            margin: 0.8rem 0;
            padding: 0.55rem 0.95rem;
        }

        div[class*="st-key-traveler-card-"] {
            background: rgba(255,255,255,0.83);
            border: 1px solid rgba(47,90,73,0.16);
            border-top: 5px solid var(--mint-dark);
            border-radius: 1.2rem;
            padding: 1rem 1.05rem 0.85rem;
        }

        .st-key-traveler-card-2,
        .st-key-traveler-card-5 {
            border-top-color: var(--nectarine) !important;
        }

        .st-key-traveler-card-3,
        .st-key-traveler-card-6 {
            border-top-color: var(--lagoon) !important;
        }

        .st-key-traveler-card-4 {
            border-top-color: var(--peach) !important;
        }

        div[class*="st-key-result-card-"] {
            background: rgba(255,255,255,0.98);
            border: 1px solid #EADBD2;
            border-top: 5px solid var(--lagoon);
            border-radius: 1.35rem;
            box-shadow: 0 10px 26px rgba(58,46,43,0.06);
            padding: 1.15rem 1.25rem 0.85rem;
            margin-bottom: 0.85rem;
        }

        div[class*="st-key-result-card-1-"] {
            border-top-color: var(--nectarine);
            box-shadow: 0 14px 34px rgba(140,70,64,0.10);
        }

        div[class*="st-key-result-card-2-"] {
            border-top-color: var(--peach);
        }

        div[class*="st-key-result-card-3-"] {
            border-top-color: var(--mint);
        }

        div[class*="st-key-result-card-"]:hover {
            box-shadow: 0 18px 42px rgba(140,70,64,0.12);
            transform: translateY(-2px);
            transition: all 160ms ease;
        }

        .ts-section-label {
            color: var(--page-primary);
            font-size: 0.76rem;
            font-weight: 800;
            letter-spacing: 0.13em;
            text-transform: uppercase;
        }

        .ts-helper {
            color: var(--muted);
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
            background: rgba(150,199,179,0.22);
            border: 1px solid rgba(47,90,73,0.14);
            border-radius: 999px;
            color: var(--mint-dark);
            display: inline-block;
            font-size: 0.76rem;
            font-weight: 800;
            padding: 0.28rem 0.58rem;
        }

        .ts-score {
            align-items: baseline;
            color: var(--nectarine-dark);
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
            color: var(--muted);
            font-size: 0.8rem;
            font-weight: 700;
        }

        .ts-tradeoff {
            background: rgba(249,185,92,0.18);
            border-left: 3px solid var(--peach);
            border-radius: 0.45rem;
            color: #66513A;
            font-size: 0.82rem;
            margin: 0.35rem 0;
            padding: 0.45rem 0.65rem;
        }

        .ts-must-do-line {
            background: rgba(215,137,127,0.13);
            border-left: 3px solid var(--nectarine);
            border-radius: 0.45rem;
            color: var(--nectarine-dark);
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
            color: var(--muted);
            font-size: 0.78rem;
            margin-top: -0.45rem;
        }

        div[data-testid="stVerticalBlockBorderWrapper"] {
            border-radius: 1.25rem;
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

            .st-key-hero-copy {
                padding: 0.5rem 0 1rem;
            }

            .st-key-hero h1 {
                font-size: clamp(2.6rem, 13vw, 3.7rem);
            }

            .ts-hero-visual {
                height: 17rem;
            }

            .st-key-trip-card,
            .st-key-travelers-shell,
            .st-key-summary-card,
            .st-key-shortlist-card {
                padding: 1.1rem 1rem 0.9rem;
            }

            .st-key-workspace-page {
                border-radius: 1.35rem;
                padding: 1rem 0.85rem 1.5rem;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


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


def _render_itinerary(
    trip: TripRequest,
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
            "Daily timing includes a 30-minute planning buffer between activities. "
            "When catalog coordinates are available, stops are ordered to reduce "
            "backtracking and show a walking estimate below."
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

        _render_overall_experience_feedback(trip, plan)


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
            st.session_state.itinerary_narrative = None
            st.session_state.itinerary_narration_error = None
            st.toast("Your itinerary is ready.")
            st.rerun()

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
    if st.button("Save this trip", icon=":material/bookmark_add:", key="save-trip"):
        saved = save_trip(
            trip,
            {
                "selected_activity_ids": st.session_state.selected_activity_ids,
                "dismissed_must_do_ids": st.session_state.dismissed_must_do_ids,
                "itinerary_plan": st.session_state.itinerary_plan,
                "rejected_activities": st.session_state.rejected_activities,
            },
            trip_id=st.session_state.saved_trip_id,
            session_id=st.session_state.feedback_session_id,
        )
        st.session_state.saved_trip_id = saved.trip_id
        st.toast(f"Saved {saved.title}")
    activities = load_activity_catalog()
    activity_by_id = _activity_lookup(activities)
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
