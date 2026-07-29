"""Unit and interaction tests for the Streamlit preference flow."""

from __future__ import annotations

import unittest

from pydantic import ValidationError
from streamlit.testing.v1 import AppTest

from src.ui import (
    build_sample_trip,
    build_trip_request,
    format_duration,
    parse_tag_text,
)


class PreferenceFlowHelpersTests(unittest.TestCase):
    def test_parse_tag_text_normalizes_and_deduplicates(self) -> None:
        self.assertEqual(
            parse_tag_text(" Colosseum, vatican museums, COLOSSEUM, "),
            ["colosseum", "vatican museums"],
        )

    def test_format_duration_uses_correct_grammar(self) -> None:
        self.assertEqual(format_duration(1), "1 hour")
        self.assertEqual(format_duration(1.5), "1.5 hours")

    def test_build_trip_request_uses_shared_validation(self) -> None:
        trip = build_trip_request(
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
                    "interests": ["history", "food"],
                    "walking_tolerance": "moderate",
                    "food_restrictions": ["vegetarian"],
                    "must_do_activities": "Colosseum",
                },
                {
                    "name": "Sam",
                    "interests": ["art"],
                    "walking_tolerance": "low",
                    "food_restrictions": [],
                    "must_do_activities": "",
                },
            ],
        )

        self.assertEqual(len(trip.travelers), 2)
        self.assertEqual(trip.travelers[0].must_do_activities, ["colosseum"])

    def test_build_trip_request_rejects_missing_interest(self) -> None:
        with self.assertRaises(ValidationError):
            build_trip_request(
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
                        "interests": [],
                        "walking_tolerance": "moderate",
                    },
                    {
                        "name": "Sam",
                        "interests": ["art"],
                        "walking_tolerance": "low",
                    },
                ],
            )

    def test_sample_trip_is_ready_for_visual_preview(self) -> None:
        trip = build_sample_trip()

        self.assertEqual(trip.destination, "Rome")
        self.assertEqual(
            [traveler.name for traveler in trip.travelers],
            ["Coco", "Sam"],
        )
        self.assertEqual(
            trip.travelers[1].must_do_activities,
            ["borghese gallery"],
        )


class StreamlitInteractionTests(unittest.TestCase):
    def test_app_opens_on_trip_basics_without_exceptions(self) -> None:
        app = AppTest.from_file("app.py")
        app.run()

        self.assertFalse(app.exception)
        self.assertEqual(
            app.title[0].value,
            "Your next great trip starts together.",
        )
        self.assertTrue(
            any(button.label == "Continue to travelers" for button in app.button)
        )

    def test_complete_preference_flow_reaches_ranked_results(self) -> None:
        app = AppTest.from_file("app.py").run()
        app.button[0].click().run()

        app.text_input(key="traveler_name_0").set_value("Coco")
        app.text_input(key="traveler_name_1").set_value("Sam")
        interest_groups = [
            group
            for group in app.get("button_group")
            if group.label == "Interests"
        ]
        interest_groups[0].select("history")
        interest_groups[1].select("art")
        next(
            button for button in app.button if button.label == "Find our best fits"
        ).click().run()

        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["planner_step"], "results")
        self.assertEqual(app.header[0].value, "Your strongest matches")
        self.assertGreaterEqual(len(app.subheader), 6)

    def test_sample_preview_reaches_ranked_results(self) -> None:
        app = AppTest.from_file("app.py").run()
        next(
            button for button in app.button if button.label == "Preview a sample group"
        ).click().run()

        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["planner_step"], "results")
        self.assertEqual(app.header[0].value, "Your strongest matches")


if __name__ == "__main__":
    unittest.main()
