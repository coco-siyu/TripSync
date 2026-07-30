"""OpenAI-backed generation for grounded TripSync narration."""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

from dotenv import load_dotenv

from openai import (
    APIConnectionError,
    AuthenticationError,
    OpenAI,
    OpenAIError,
    RateLimitError,
)
from pydantic import ValidationError

from src.models import Activity, ItineraryPlan, TripRequest
from src.narration import (
    ItineraryNarrative,
    NarrationGroundingError,
    validate_narrative_against_plan,
)
from src.prompts import (
    build_itinerary_change_input,
    build_itinerary_narration_input,
)
from src.proposals import (
    ItineraryChangeProposals,
    ProposalGroundingError,
    validate_proposals_against_plan,
)


DEFAULT_OPENAI_MODEL = "gpt-5.6-luna"


load_dotenv()


class NarrationConfigurationError(RuntimeError):
    """Raised when local OpenAI configuration is incomplete."""


class NarrationGenerationError(RuntimeError):
    """Raised when a narration request does not produce usable output."""


def configured_model() -> str:
    """Return the optional environment override or the cost-aware default."""

    return os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip()


def _default_client() -> OpenAI:
    if not os.getenv("OPENAI_API_KEY", "").strip():
        raise NarrationConfigurationError(
            "OPENAI_API_KEY is missing. Add it to the local .env file."
        )
    return OpenAI()


def generate_itinerary_narrative(
    trip: TripRequest,
    activities: Sequence[Activity],
    plan: ItineraryPlan,
    *,
    client: Any | None = None,
    model: str | None = None,
) -> ItineraryNarrative:
    """Generate and validate an LLM narrative without changing the plan."""

    active_client = client if client is not None else _default_client()
    selected_model = (model or configured_model()).strip()
    if not selected_model:
        raise NarrationConfigurationError(
            "OPENAI_MODEL cannot be blank when it is configured."
        )

    try:
        response = active_client.responses.parse(
            model=selected_model,
            input=build_itinerary_narration_input(
                trip,
                activities,
                plan,
            ),
            text_format=ItineraryNarrative,
        )
    except AuthenticationError as error:
        raise NarrationGenerationError(
            "OpenAI rejected the API key. Check OPENAI_API_KEY in .env."
        ) from error
    except RateLimitError as error:
        raise NarrationGenerationError(
            "OpenAI quota or rate limit reached. Check API billing and "
            "usage limits before retrying."
        ) from error
    except APIConnectionError as error:
        raise NarrationGenerationError(
            "Could not connect to OpenAI. Check the network and retry."
        ) from error
    except OpenAIError as error:
        raise NarrationGenerationError(
            f"OpenAI narration request failed: {type(error).__name__}"
        ) from error

    parsed = getattr(response, "output_parsed", None)
    if parsed is None:
        raise NarrationGenerationError(
            "OpenAI returned no parsed itinerary narrative."
        )

    try:
        narrative = (
            parsed
            if isinstance(parsed, ItineraryNarrative)
            else ItineraryNarrative.model_validate(parsed)
        )
        return validate_narrative_against_plan(narrative, plan)
    except (ValidationError, NarrationGroundingError) as error:
        raise NarrationGenerationError(
            f"OpenAI narration failed grounding validation: {error}"
        ) from error


def generate_itinerary_change_proposals(
    trip: TripRequest,
    activities: Sequence[Activity],
    plan: ItineraryPlan,
    request: str,
    *,
    client: Any | None = None,
    model: str | None = None,
) -> ItineraryChangeProposals:
    """Generate grounded, reviewable change options for an itinerary."""

    if not request.strip():
        raise NarrationConfigurationError(
            "Describe what you would like to adjust before requesting ideas."
        )
    active_client = client if client is not None else _default_client()
    selected_model = (model or configured_model()).strip()
    if not selected_model:
        raise NarrationConfigurationError(
            "OPENAI_MODEL cannot be blank when it is configured."
        )

    try:
        response = active_client.responses.parse(
            model=selected_model,
            input=build_itinerary_change_input(
                trip,
                activities,
                plan,
                request.strip(),
            ),
            text_format=ItineraryChangeProposals,
        )
    except AuthenticationError as error:
        raise NarrationGenerationError(
            "OpenAI rejected the API key. Check OPENAI_API_KEY in .env."
        ) from error
    except RateLimitError as error:
        raise NarrationGenerationError(
            "OpenAI quota or rate limit reached. Check API billing and "
            "usage limits before retrying."
        ) from error
    except APIConnectionError as error:
        raise NarrationGenerationError(
            "Could not connect to OpenAI. Check the network and retry."
        ) from error
    except OpenAIError as error:
        raise NarrationGenerationError(
            f"OpenAI change request failed: {type(error).__name__}"
        ) from error
    except ValidationError as error:
        raise NarrationGenerationError(
            "OpenAI returned adjustment options in an unusable format. "
            "Please try again."
        ) from error

    parsed = getattr(response, "output_parsed", None)
    if parsed is None:
        raise NarrationGenerationError(
            "OpenAI returned no parsed itinerary change proposals."
        )

    try:
        proposals = (
            parsed
            if isinstance(parsed, ItineraryChangeProposals)
            else ItineraryChangeProposals.model_validate(parsed)
        )
        return validate_proposals_against_plan(proposals, plan, activities)
    except (ValidationError, ProposalGroundingError) as error:
        raise NarrationGenerationError(
            f"OpenAI change proposals failed grounding validation: {error}"
        ) from error
