"""Tests for grounded itinerary narration and OpenAI integration."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from openai import RateLimitError

from src.llm import (
    DEFAULT_OPENAI_MODEL,
    NarrationConfigurationError,
    NarrationGenerationError,
    generate_itinerary_narrative,
)
from src.models import Activity, ItineraryPlan, TravelerProfile, TripRequest
from src.narration import (
    ItineraryNarrative,
    NarratedActivity,
    NarratedDay,
    NarrationGroundingError,
    validate_narrative_against_plan,
)
from src.planner import build_itinerary
from src.prompts import build_itinerary_narration_input
from src.scoring import rank_activities


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DATA_PATH = REPOSITORY_ROOT / "data" / "sample_activities.json"


def make_trip() -> TripRequest:
    return TripRequest(
        destination="Rome",
        country="Italy",
        days=2,
        budget_level="moderate",
        pace="balanced",
        travelers=[
            TravelerProfile(
                name="Coco",
                interests=["history", "food"],
                walking_tolerance="moderate",
                must_do_activities=["Colosseum"],
            ),
            TravelerProfile(
                name="Sam",
                interests=["art", "architecture"],
                walking_tolerance="low",
            ),
        ],
    )


def make_narrative(plan: ItineraryPlan) -> ItineraryNarrative:
    return ItineraryNarrative(
        trip_summary="A grounded Rome plan shaped around the group.",
        days=[
            NarratedDay(
                day_number=day.day_number,
                summary=f"A concise introduction to Day {day.day_number}.",
                activities=[
                    NarratedActivity(
                        activity_id=activity.activity_id,
                        why_it_fits=(
                            f"{activity.activity_name} reflects the "
                            "group's validated preferences."
                        ),
                        practical_note="Timing is an estimate.",
                    )
                    for activity in day.activities
                ],
                tradeoffs=[],
            )
            for day in plan.days
        ],
        overall_tradeoffs=[
            "Opening hours and live availability are not included."
        ],
    )


class _FakeResponses:
    def __init__(self, parsed: object) -> None:
        self.parsed = parsed
        self.calls: list[dict[str, object]] = []

    def parse(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(output_parsed=self.parsed)


class _FakeClient:
    def __init__(self, parsed: object) -> None:
        self.responses = _FakeResponses(parsed)


class _RaisingResponses:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def parse(self, **kwargs: object) -> SimpleNamespace:
        raise self.error


class _RaisingClient:
    def __init__(self, error: Exception) -> None:
        self.responses = _RaisingResponses(error)


class NarrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        payload = json.loads(SAMPLE_DATA_PATH.read_text(encoding="utf-8"))
        cls.activities = [Activity.model_validate(item) for item in payload]
        cls.trip = make_trip()
        cls.results = rank_activities(cls.activities, cls.trip)
        cls.plan = build_itinerary(
            cls.trip,
            cls.activities,
            cls.results,
            ["rome_colosseum"],
            must_do_owners_by_activity_id={
                "rome_colosseum": ("Coco",),
            },
            auto_fill=False,
        )

    def test_prompt_contains_only_scheduled_activity_context(self) -> None:
        request_input = build_itinerary_narration_input(
            self.trip,
            self.activities,
            self.plan,
        )
        serialized_input = json.dumps(request_input)

        self.assertIn("rome_colosseum", serialized_input)
        self.assertIn("Colosseum", serialized_input)
        self.assertNotIn("rome_vatican_museums", serialized_input)
        self.assertIn("schedule is immutable", serialized_input)

    def test_prompt_rejects_missing_activity_context(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "missing from narration context",
        ):
            build_itinerary_narration_input(
                self.trip,
                [],
                self.plan,
            )

    def test_valid_narrative_matches_the_plan(self) -> None:
        narrative = make_narrative(self.plan)

        self.assertIs(
            validate_narrative_against_plan(narrative, self.plan),
            narrative,
        )

    def test_invented_activity_is_rejected(self) -> None:
        payload = make_narrative(self.plan).model_dump(mode="json")
        payload["days"][0]["activities"][0][
            "activity_id"
        ] = "rome_invented_place"
        narrative = ItineraryNarrative.model_validate(payload)

        with self.assertRaisesRegex(
            NarrationGroundingError,
            "must match the itinerary",
        ):
            validate_narrative_against_plan(narrative, self.plan)

    def test_changed_day_order_is_rejected(self) -> None:
        narrative = make_narrative(self.plan)
        changed = narrative.model_copy(
            update={"days": list(reversed(narrative.days))}
        )

        with self.assertRaisesRegex(
            NarrationGroundingError,
            "days must match",
        ):
            validate_narrative_against_plan(changed, self.plan)

    def test_client_uses_responses_parse_and_structured_output(self) -> None:
        narrative = make_narrative(self.plan)
        client = _FakeClient(narrative)

        result = generate_itinerary_narrative(
            self.trip,
            self.activities,
            self.plan,
            client=client,
            model="test-model",
        )

        self.assertEqual(result, narrative)
        call = client.responses.calls[0]
        self.assertEqual(call["model"], "test-model")
        self.assertIs(call["text_format"], ItineraryNarrative)
        self.assertEqual(len(call["input"]), 2)

    def test_client_rejects_ungrounded_parsed_output(self) -> None:
        payload = make_narrative(self.plan).model_dump(mode="json")
        payload["days"][0]["activities"][0][
            "activity_id"
        ] = "rome_invented_place"
        client = _FakeClient(payload)

        with self.assertRaisesRegex(
            NarrationGenerationError,
            "failed grounding validation",
        ):
            generate_itinerary_narrative(
                self.trip,
                self.activities,
                self.plan,
                client=client,
            )

    def test_client_rejects_missing_parsed_output(self) -> None:
        client = _FakeClient(None)

        with self.assertRaisesRegex(
            NarrationGenerationError,
            "no parsed itinerary narrative",
        ):
            generate_itinerary_narrative(
                self.trip,
                self.activities,
                self.plan,
                client=client,
            )

    def test_quota_error_has_actionable_message(self) -> None:
        request = httpx.Request("POST", "https://api.openai.com/v1/responses")
        response = httpx.Response(429, request=request)
        client = _RaisingClient(
            RateLimitError(
                "insufficient quota",
                response=response,
                body=None,
            )
        )

        with self.assertRaisesRegex(
            NarrationGenerationError,
            "quota or rate limit reached",
        ):
            generate_itinerary_narrative(
                self.trip,
                self.activities,
                self.plan,
                client=client,
            )

    def test_missing_api_key_has_friendly_error(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("src.llm.load_dotenv"),
        ):
            with self.assertRaisesRegex(
                NarrationConfigurationError,
                "OPENAI_API_KEY is missing",
            ):
                generate_itinerary_narrative(
                    self.trip,
                    self.activities,
                    self.plan,
                )

    def test_default_model_is_the_evaluated_gpt_5_6_option(self) -> None:
        self.assertEqual(DEFAULT_OPENAI_MODEL, "gpt-5.6-terra")


if __name__ == "__main__":
    unittest.main()
