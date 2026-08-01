"""Tests for grounded itinerary adjustment proposals."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.llm import (
    NarrationGenerationError,
    generate_itinerary_change_proposals,
)
from src.models import Activity, TravelerProfile, TripRequest
from src.planner import apply_itinerary_change_proposal, build_itinerary
from src.prompts import build_itinerary_change_input
from src.proposals import (
    ItineraryChangeProposal,
    ItineraryChangeProposals,
    ProposalGroundingError,
    validate_proposals_against_plan,
)
from src.scoring import rank_activities


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DATA_PATH = REPOSITORY_ROOT / "data" / "sample_activities.json"


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


class ChangeProposalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.activities = [
            Activity.model_validate(item)
            for item in json.loads(SAMPLE_DATA_PATH.read_text(encoding="utf-8"))
        ]
        cls.trip = TripRequest(
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
                ),
                TravelerProfile(
                    name="Sam",
                    interests=["art", "architecture"],
                    walking_tolerance="low",
                ),
            ],
        )
        cls.ranked = rank_activities(cls.activities, cls.trip)
        cls.plan = build_itinerary(
            cls.trip,
            cls.activities,
            cls.ranked,
            ["rome_colosseum"],
            auto_fill=False,
        )
        cls.scheduled_ids = {
            activity.activity_id
            for day in cls.plan.days
            for activity in day.activities
        }
        cls.eligible = [
            activity
            for activity in cls.activities
            if activity.id not in cls.scheduled_ids
        ]
        cls.target_day = next(day for day in cls.plan.days if day.activities)
        cls.removed_id = cls.target_day.activities[0].activity_id
        removed_duration = cls.target_day.activities[0].duration_hours
        cls.added_id = next(
            activity.id
            for activity in cls.eligible
            if activity.duration_hours <= removed_duration
        )

    def _proposal(self) -> ItineraryChangeProposal:
        return ItineraryChangeProposal(
            title="A calmer swap",
            operation="replace",
            day_number=self.target_day.day_number,
            remove_activity_id=self.removed_id,
            add_activity_id=self.added_id,
            rationale="This exchange better reflects the group's request.",
            tradeoffs=["It changes one planned highlight."],
        )

    def test_prompt_excludes_scheduled_ids_from_replacement_catalog(self) -> None:
        messages = build_itinerary_change_input(
            self.trip,
            self.activities,
            self.plan,
            "Make Day 1 calmer.",
        )

        self.assertIn("Make Day 1 calmer.", messages[1]["content"])
        self.assertIn(self.added_id, messages[1]["content"])
        self.assertIn(self.removed_id, messages[1]["content"])
        context = json.loads(
            messages[1]["content"].split("grounded context:\n", maxsplit=1)[1]
        )
        replacement_ids = {
            activity["id"] for activity in context["eligible_replacement_activities"]
        }
        self.assertNotIn(self.removed_id, replacement_ids)
        self.assertTrue(replacement_ids.isdisjoint(self.scheduled_ids))

    def test_validation_rejects_a_change_on_the_wrong_day(self) -> None:
        invalid = self._proposal().model_copy(
            update={"day_number": 2}
        )
        proposals = ItineraryChangeProposals(
            acknowledgement="Here are grounded options.",
            proposals=[invalid],
        )

        with self.assertRaises(ProposalGroundingError):
            validate_proposals_against_plan(proposals, self.plan, self.eligible)

    def test_llm_uses_structured_proposal_schema(self) -> None:
        proposals = ItineraryChangeProposals(
            acknowledgement="Here is a grounded option.",
            proposals=[self._proposal()],
        )
        client = _FakeClient(proposals)

        returned = generate_itinerary_change_proposals(
            self.trip,
            self.eligible,
            self.plan,
            "Make the first day calmer.",
            client=client,
        )

        self.assertEqual(returned, proposals)
        self.assertIs(
            client.responses.calls[0]["text_format"],
            ItineraryChangeProposals,
        )

    def test_allows_alternative_options_for_the_same_stop(self) -> None:
        alternatives = ItineraryChangeProposals(
            acknowledgement="Here are two ways to make the day calmer.",
            proposals=[
                self._proposal(),
                self._proposal().model_copy(
                    update={"title": "Leave the time open", "operation": "remove", "add_activity_id": None}
                ),
            ],
        )

        self.assertEqual(len(alternatives.proposals), 2)

    def test_planner_applies_only_the_approved_same_day_change(self) -> None:
        outcome = apply_itinerary_change_proposal(
            self.plan,
            self._proposal(),
            self.activities,
            self.ranked,
        )

        changed_day = outcome.plan.days[self.target_day.day_number - 1]
        self.assertEqual(changed_day.activities[0].activity_id, self.added_id)
        self.assertNotIn(
            self.removed_id,
            [activity.activity_id for activity in changed_day.activities],
        )
        self.assertTrue(
            all(
                day.planned_hours <= day.capacity_hours
                for day in outcome.plan.days
            )
        )

    def test_planner_adds_an_eligible_activity_when_the_day_has_capacity(self) -> None:
        addition = next(
            activity
            for activity in self.eligible
            if (
                activity.duration_hours + 0.5 + self.target_day.planned_hours
                <= self.target_day.capacity_hours
            )
        )
        proposal = ItineraryChangeProposal(
            title="Fill an open part of the day",
            operation="add",
            day_number=self.target_day.day_number,
            add_activity_id=addition.id,
            rationale="This catalog activity fits in the day's remaining time.",
        )

        outcome = apply_itinerary_change_proposal(
            self.plan,
            proposal,
            self.activities,
            self.ranked,
        )

        changed_day = outcome.plan.days[self.target_day.day_number - 1]
        self.assertIsNone(outcome.removed_activity)
        self.assertEqual(outcome.replacement_activity.activity_id, addition.id)
        self.assertIn(
            addition.id,
            [activity.activity_id for activity in changed_day.activities],
        )

    def test_add_proposal_cannot_include_a_removal(self) -> None:
        with self.assertRaises(ValueError):
            ItineraryChangeProposal(
                title="Invalid mixed action",
                operation="add",
                day_number=self.target_day.day_number,
                remove_activity_id=self.removed_id,
                add_activity_id=self.added_id,
                rationale="An add must not remove another activity.",
            )

    def test_llm_rejects_ungrounded_proposals(self) -> None:
        invalid = ItineraryChangeProposals(
            acknowledgement="Here is an option.",
            proposals=[
                self._proposal().model_copy(
                    update={"add_activity_id": "rome_unknown_activity"}
                )
            ],
        )

        with self.assertRaises(NarrationGenerationError):
            generate_itinerary_change_proposals(
                self.trip,
                self.eligible,
                self.plan,
                "Give me something different.",
                client=_FakeClient(invalid),
            )


if __name__ == "__main__":
    unittest.main()
