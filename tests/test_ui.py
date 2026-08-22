"""Unit and interaction tests for the Streamlit preference flow."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError
from streamlit.testing.v1 import AppTest

from src.models import ItineraryPlan, ItinerarySource
from src.narration import ItineraryNarrative, NarratedActivity, NarratedDay
from src.llm import NarrationGenerationError
from src.proposals import ItineraryChangeProposal, ItineraryChangeProposals
from src.catalog import load_curated_activities
from src.search import retrieve_activities as real_retrieve_activities
from src.trips import SavedTrip
from src.ui import (
    build_sample_trip,
    build_trip_request,
    catalog_city_options,
    catalog_destination_options,
    combine_interest_tags,
    format_duration,
    parse_tag_text,
    split_destination,
)


class PreferenceFlowHelpersTests(unittest.TestCase):
    def test_combine_interest_tags_keeps_preset_and_typed_entries(self) -> None:
        self.assertEqual(
            combine_interest_tags(["art"], ["Renaissance painting", " art "]),
            ["art", "renaissance painting"],
        )

    def test_parse_tag_text_normalizes_and_deduplicates(self) -> None:
        self.assertEqual(
            parse_tag_text(" Colosseum, vatican museums, COLOSSEUM, "),
            ["colosseum", "vatican museums"],
        )

    def test_format_duration_uses_correct_grammar(self) -> None:
        self.assertEqual(format_duration(1), "1 hour")
        self.assertEqual(format_duration(1.5), "1.5 hours")

    def test_catalog_destination_options_are_deduplicated_and_counted(self) -> None:
        activities = load_curated_activities()
        destinations = catalog_destination_options(activities)

        self.assertIn("Rome, Italy", destinations)
        self.assertGreater(destinations["Rome, Italy"], 0)
        self.assertEqual(
            sum(destinations.values()),
            len(activities),
        )

    def test_catalog_city_options_are_names_without_activity_counts(self) -> None:
        activities = load_curated_activities()
        destinations = [
            (activity.city, activity.country)
            for activity in activities
        ]
        cities = catalog_city_options(destinations)

        self.assertIn("Rome", cities)
        self.assertEqual(cities["Rome"], "Italy")
        self.assertFalse(any("activities" in city for city in cities))

    def test_split_destination_supports_catalog_and_custom_values(self) -> None:
        self.assertEqual(split_destination("Tokyo, Japan"), ("Tokyo", "Japan"))
        self.assertEqual(split_destination("Tokyo"), ("Tokyo", ""))

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
    @staticmethod
    def _sample_results_app() -> AppTest:
        sample_trip = build_sample_trip()
        app = AppTest.from_file("app.py")
        app.session_state["planner_step"] = "results"
        app.session_state["trip_basics"] = sample_trip.model_dump(
            mode="json",
            exclude={"travelers"},
        )
        app.session_state["trip_request"] = sample_trip.model_dump(mode="json")
        app.session_state["selected_activity_ids"] = []
        app.session_state["dismissed_must_do_ids"] = []
        return app.run(timeout=10)

    def test_app_opens_on_trip_basics_without_exceptions(self) -> None:
        with patch(
            "src.ui.load_activity_catalog",
            side_effect=AssertionError(
                "trip basics must not load the full activity catalog"
            ),
        ):
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

    def test_destination_panel_starts_local_then_refreshes_remote_index(
        self,
    ) -> None:
        with (
            patch(
                "src.ui.load_packaged_destination_index",
                return_value=[("Rome", "Italy")],
            ) as packaged_mock,
            patch(
                "src.ui.load_destination_index",
                return_value=[
                    ("Belgrade", "Serbia"),
                    ("Rome", "Italy"),
                ],
            ) as remote_mock,
        ):
            app = AppTest.from_file("app.py").run()

        self.assertFalse(app.exception)
        destination_search = next(
            item for item in app.selectbox if item.label == "Destination city"
        )
        self.assertIn("Belgrade", destination_search.options)
        self.assertTrue(
            app.session_state["catalog_destination_index_refreshed"]
        )
        packaged_mock.assert_called_once_with()
        remote_mock.assert_called_once_with()

    def test_destination_search_uses_prefix_suggestions_and_accepts_custom_city(
        self,
    ) -> None:
        app = AppTest.from_file("app.py").run()
        destination_search = next(
            item
            for item in app.selectbox
            if item.label == "Destination city"
        )

        self.assertIn("Rome", destination_search.options)
        self.assertNotIn("Rome · 17 activities", destination_search.options)
        self.assertTrue(destination_search.proto.accept_new_options)
        self.assertEqual(destination_search.proto.filter_mode, 2)

        next(
            item for item in app.text_input if item.label == "Country"
        ).set_value("Japan").run()
        next(
            item
            for item in app.selectbox
            if item.label == "Destination city"
        ).select("Florence").run()

        self.assertEqual(
            next(
                item
                for item in app.selectbox
                if item.label == "Destination city"
            ).value,
            "Florence",
        )
        self.assertEqual(
            next(item for item in app.text_input if item.label == "Country").value,
            "Italy",
        )

    def test_plan_a_trip_navigation_starts_a_distinct_blank_trip(self) -> None:
        app = self._sample_results_app()
        app.session_state["saved_trip_id"] = "saved-rome-trip"
        app.session_state["selected_activity_ids"] = ["rome_colosseum"]
        app.session_state["traveler_name_0"] = "Coco"

        next(
            button for button in app.button if button.label == "Plan a trip"
        ).click().run(timeout=10)

        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["app_workspace"], "Plan a trip")
        self.assertEqual(app.session_state["planner_step"], "trip")
        self.assertIsNone(app.session_state["trip_request"])
        self.assertIsNone(app.session_state["saved_trip_id"])
        self.assertIsNone(app.session_state["saved_itinerary_version_id"])
        self.assertEqual(app.session_state["selected_activity_ids"], [])
        self.assertNotIn("traveler_name_0", app.session_state)
        self.assertIsNone(
            next(
                item
                for item in app.selectbox
                if item.label == "Destination city"
            ).value
        )
        self.assertEqual(
            next(item for item in app.text_input if item.label == "Country").value,
            "",
        )

    def test_complete_preference_flow_reaches_ranked_results(self) -> None:
        app = AppTest.from_file("app.py").run()
        next(
            button
            for button in app.button
            if button.label == "Continue to travelers"
        ).click().run()

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

    def test_saved_trip_selector_starts_a_new_itinerary_empty(self) -> None:
        rome = build_sample_trip()
        paris = rome.model_copy(
            update={"destination": "Paris", "country": "France"}
        )
        records = [
            SavedTrip(
                trip_id="rome-trip",
                title="Rome · 3 days",
                trip=rome,
                state={},
                updated_at="2026-08-22T05:05:00+00:00",
            ),
            SavedTrip(
                trip_id="paris-trip",
                title="Paris · 3 days",
                trip=paris,
                state={},
                updated_at="2026-08-22T04:05:00+00:00",
            ),
        ]

        with patch("src.trips_ui.list_saved_trips", return_value=records):
            app = AppTest.from_file("app.py")
            app.session_state["app_workspace"] = "My trips"
            app.run(timeout=10)

            trip_selector = next(
                box for box in app.selectbox if box.label == "Choose a trip"
            )
            self.assertEqual(len(trip_selector.options), 2)
            self.assertEqual(
                [heading.value for heading in app.subheader],
                ["Rome · 3 days"],
            )

            trip_selector.select("paris-trip").run(timeout=10)
            self.assertEqual(
                [heading.value for heading in app.subheader],
                ["Paris · 3 days"],
            )

            next(
                box for box in app.selectbox if box.label == "Choose a trip"
            ).select("rome-trip").run(timeout=10)
            next(
                button
                for button in app.button
                if button.label == "Create new itinerary"
            ).click().run(timeout=10)

        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["app_workspace"], "Plan a trip")
        self.assertEqual(app.session_state["saved_trip_id"], "rome-trip")
        self.assertEqual(app.session_state["selected_activity_ids"], [])
        self.assertFalse(app.session_state["auto_select_must_dos"])
        self.assertIsNone(app.session_state["itinerary_plan"])

    def test_results_views_are_ordered_and_must_dos_initialize_shortlist(
        self,
    ) -> None:
        app = self._sample_results_app()
        results_view = next(
            group
            for group in app.get("button_group")
            if group.key == "results_view"
        )

        self.assertEqual(
            results_view.options,
            [
                "Top 5",
                "Must-dos (2)",
                "All activities",
                "Rejected (0)",
            ],
        )
        self.assertEqual(
            app.session_state["selected_activity_ids"],
            ["rome_colosseum", "rome_borghese_gallery"],
        )
        self.assertTrue(
            any(
                markdown.value
                == '<div class="ts-must-do-line">★ Must-do for Sam</div>'
                for markdown in app.markdown
            )
        )

    def test_results_explain_the_hybrid_retrieval_stage(self) -> None:
        app = self._sample_results_app()

        self.assertTrue(
            any(
                caption.value.startswith((
                    "Hybrid retrieval found ",
                    "Text retrieval fallback found ",
                ))
                and caption.value.endswith(" destination records.")
                for caption in app.caption
            )
        )
        self.assertTrue(
            any(
                markdown.value == "**Why it was retrieved**"
                for markdown in app.markdown
            )
        )
        self.assertTrue(
            any(
                caption.value == "Must-do for Sam"
                for caption in app.caption
            )
        )

    def test_unknown_destination_shows_catalog_guardrail(self) -> None:
        trip = build_sample_trip().model_copy(
            update={"destination": "Tokyo", "country": "Japan"}
        )
        app = AppTest.from_file("app.py")
        app.session_state["planner_step"] = "results"
        app.session_state["trip_request"] = trip.model_dump(mode="json")
        app.session_state["selected_activity_ids"] = []
        app.session_state["dismissed_must_do_ids"] = []
        app.run()

        self.assertFalse(app.exception)
        self.assertTrue(
            any(
                "no curated activities for Tokyo, Japan" in warning.value
                for warning in app.warning
            )
        )
        self.assertFalse(
            any(header.value == "Your trip shortlist" for header in app.header)
        )

        next(
            button
            for button in app.button
            if button.label == "Change destination"
        ).click().run()

        self.assertEqual(app.session_state["planner_step"], "trip")
        self.assertEqual(
            next(
                item
                for item in app.selectbox
                if item.label == "Destination city"
            ).value,
            "Tokyo",
        )
        self.assertEqual(
            next(
                item
                for item in app.text_input
                if item.label == "Country"
            ).value,
            "Japan",
        )

    def test_must_do_view_filters_cards_without_changing_shortlist(self) -> None:
        app = self._sample_results_app()
        results_view = next(
            group
            for group in app.get("button_group")
            if group.key == "results_view"
        )
        results_view.select("Must-dos (2)").run()

        self.assertEqual(app.session_state["results_view"], "must_dos")
        self.assertEqual(
            [subheader.value for subheader in app.subheader[1:]],
            ["🏛️ Colosseum", "🎨 Borghese Gallery"],
        )
        self.assertEqual(
            app.session_state["selected_activity_ids"],
            ["rome_colosseum", "rome_borghese_gallery"],
        )

    def test_added_recommendation_persists_and_removed_must_do_stays_removed(
        self,
    ) -> None:
        app = self._sample_results_app()
        app.button(key="activity-selection-rome_pantheon").click().run()

        self.assertIn(
            "rome_pantheon",
            app.session_state["selected_activity_ids"],
        )

        app.button(key="shortlist-remove-rome_colosseum").click().run()

        self.assertNotIn(
            "rome_colosseum",
            app.session_state["selected_activity_ids"],
        )
        self.assertIn(
            "rome_colosseum",
            app.session_state["dismissed_must_do_ids"],
        )

    def test_activity_details_can_open_and_close_without_changing_shortlist(
        self,
    ) -> None:
        app = self._sample_results_app()
        detail_button = next(
            button for button in app.button if button.label == "View details"
        )

        detail_button.click().run()

        selected_detail_id = app.session_state["activity_detail_id"]
        self.assertIsNotNone(selected_detail_id)
        self.assertTrue(any(button.label == "Hide details" for button in app.button))
        self.assertEqual(
            app.session_state["selected_activity_ids"],
            ["rome_colosseum", "rome_borghese_gallery"],
        )

        next(button for button in app.button if button.label == "Hide details").click().run()

        self.assertIsNone(app.session_state["activity_detail_id"])
        self.assertTrue(any(button.label == "View details" for button in app.button))

    def test_results_retrieval_is_reused_when_shortlisting_and_planning(
        self,
    ) -> None:
        """Small result actions must not trigger repeat embedding retrieval."""

        with patch(
            "src.ui.retrieve_activities",
            wraps=real_retrieve_activities,
        ) as retrieve:
            app = self._sample_results_app()
            self.assertEqual(retrieve.call_count, 1)

            app.button(key="activity-selection-rome_pantheon").click().run()
            self.assertEqual(retrieve.call_count, 1)

            app.button(key="build-itinerary").click().run()
            self.assertEqual(retrieve.call_count, 1)

    def test_build_itinerary_creates_three_guarded_days(self) -> None:
        app = self._sample_results_app()
        app.button(key="build-itinerary").click().run()
        plan = ItineraryPlan.model_validate(
            app.session_state["itinerary_plan"]
        )

        self.assertEqual(len(plan.days), 3)
        self.assertGreater(
            sum(len(day.activities) for day in plan.days),
            2,
        )
        self.assertTrue(
            all(
                day.planned_hours <= day.capacity_hours
                for day in plan.days
            )
        )

    def test_itinerary_save_button_persists_trip_and_version(self) -> None:
        app = self._sample_results_app()
        app.button(key="build-itinerary").click().run()

        with patch("src.ui.save_trip") as save:
            save.return_value = SimpleNamespace(
                trip_id="saved-trip",
                title="Rome, Italy · 3 days",
            )
            app.button(key="save-itinerary").click().run()

        self.assertEqual(app.session_state["saved_trip_id"], "saved-trip")
        self.assertTrue(save.call_args.kwargs["save_itinerary_version"])
        saved_state = save.call_args.args[1]
        self.assertEqual(
            saved_state["itinerary_plan"],
            app.session_state["itinerary_plan"],
        )

    def test_itinerary_offers_a_grounded_adjustment_request(self) -> None:
        app = self._sample_results_app()
        app.button(key="build-itinerary").click().run()

        self.assertTrue(
            any(
                text_input.key == "itinerary-change-request-text"
                for text_input in app.text_input
            )
        )
        self.assertTrue(
            any(
                button.label == "Suggest adjustments"
                for button in app.button
            )
        )
        self.assertTrue(
            all(
                f"Day {day_number}" in [
                    subheader.value for subheader in app.subheader
                ]
                for day_number in range(1, 4)
            )
        )
        self.assertTrue(
            any(
                text_area.label == "Optional overall feedback"
                for text_area in app.text_area
            )
        )

    def test_approved_adjustment_proposal_updates_the_itinerary(self) -> None:
        app = self._sample_results_app()
        app.button(key="build-itinerary").click().run()
        before = ItineraryPlan.model_validate(app.session_state["itinerary_plan"])
        scheduled_ids = {
            activity.activity_id
            for day in before.days
            for activity in day.activities
        }
        target_day = next(day for day in before.days if day.activities)
        removed_id = target_day.activities[-1].activity_id
        catalog = load_curated_activities()
        replacement = next(
            activity
            for activity in catalog
            if activity.city == "Rome"
            and activity.id not in scheduled_ids
            and activity.duration_hours <= target_day.activities[-1].duration_hours
        )
        proposal = ItineraryChangeProposal(
            title="A tested replacement",
            operation="replace",
            day_number=target_day.day_number,
            remove_activity_id=removed_id,
            add_activity_id=replacement.id,
            rationale="A grounded replacement for the same day.",
        )
        app.session_state["itinerary_change_proposals"] = (
            ItineraryChangeProposals(
                acknowledgement="Here is a grounded option.",
                proposals=[proposal],
            ).model_dump(mode="json")
        )
        app.run()

        app.button(key="apply-itinerary-proposal-1").click().run()
        after = ItineraryPlan.model_validate(app.session_state["itinerary_plan"])

        self.assertIsNone(app.session_state["itinerary_change_proposals"])
        self.assertNotIn(
            removed_id,
            [activity.activity_id for day in after.days for activity in day.activities],
        )
        self.assertIn(
            replacement.id,
            [activity.activity_id for day in after.days for activity in day.activities],
        )

    def test_pace_override_requires_confirmation_then_applies(self) -> None:
        app = self._sample_results_app()
        app.button(key="build-itinerary").click().run()
        plan = ItineraryPlan.model_validate(app.session_state["itinerary_plan"])
        scheduled_ids = {
            activity.activity_id
            for day in plan.days
            for activity in day.activities
        }
        catalog = load_curated_activities()
        target_day, removed, replacement = next(
            (day, scheduled, candidate)
            for day in plan.days
            for scheduled in day.activities
            for candidate in catalog
            if candidate.city == "Rome"
            and candidate.id not in scheduled_ids
            and day.planned_hours - scheduled.duration_hours + candidate.duration_hours
            > day.capacity_hours
        )
        app.session_state["itinerary_change_proposals"] = (
            ItineraryChangeProposals(
                acknowledgement="Here is a too-long option.",
                proposals=[
                    ItineraryChangeProposal(
                        title="An option that does not fit",
                        operation="replace",
                        day_number=target_day.day_number,
                        remove_activity_id=removed.activity_id,
                        add_activity_id=replacement.id,
                        rationale="This deliberately exceeds the day capacity.",
                    )
                ],
            ).model_dump(mode="json")
        )
        app.run()

        self.assertIsNotNone(app.session_state["itinerary_change_proposals"])
        self.assertTrue(
            any(
                "would be" in warning.value
                for warning in app.warning
            )
        )
        apply_button = app.button(key="apply-itinerary-proposal-1")
        self.assertTrue(apply_button.disabled)
        self.assertEqual(apply_button.label, "Apply anyway")

        app.checkbox(key="pace-override-confirm-1").set_value(True).run()
        app.button(key="apply-itinerary-proposal-1").click().run()
        updated_plan = ItineraryPlan.model_validate(
            app.session_state["itinerary_plan"]
        )
        updated_day = next(
            day
            for day in updated_plan.days
            if day.day_number == target_day.day_number
        )
        self.assertTrue(updated_day.pace_override_approved)
        self.assertGreater(updated_day.planned_hours, updated_day.capacity_hours)

    def test_itinerary_can_use_only_the_shortlist(self) -> None:
        app = self._sample_results_app()
        app.toggle(key="itinerary_auto_fill").set_value(False).run()
        app.button(key="build-itinerary").click().run()
        plan = ItineraryPlan.model_validate(
            app.session_state["itinerary_plan"]
        )
        scheduled = [
            activity
            for day in plan.days
            for activity in day.activities
        ]

        self.assertEqual(len(scheduled), 2)
        self.assertTrue(
            all(
                activity.source == ItinerarySource.SHORTLIST
                for activity in scheduled
            )
        )

    def test_shortlist_change_invalidates_generated_itinerary(self) -> None:
        app = self._sample_results_app()
        app.button(key="build-itinerary").click().run()
        self.assertIsNotNone(app.session_state["itinerary_plan"])
        app.session_state["itinerary_narrative"] = {"saved": "story"}

        app.button(key="shortlist-remove-rome_colosseum").click().run()

        self.assertIsNone(app.session_state["itinerary_plan"])
        self.assertIsNone(app.session_state["itinerary_narrative"])

    def test_itinerary_shows_a_grounded_trip_story_when_available(self) -> None:
        app = self._sample_results_app()
        app.button(key="build-itinerary").click().run()
        plan = ItineraryPlan.model_validate(
            app.session_state["itinerary_plan"]
        )
        narrative = ItineraryNarrative(
            trip_summary="A Rome story built around shared interests.",
            days=[
                NarratedDay(
                    day_number=day.day_number,
                    summary=f"Day {day.day_number} has a clear rhythm.",
                    activities=[
                        NarratedActivity(
                            activity_id=activity.activity_id,
                            why_it_fits="It reflects the group's preferences.",
                        )
                        for activity in day.activities
                    ],
                )
                for day in plan.days
            ],
        )
        app.session_state["itinerary_narrative"] = narrative.model_dump(
            mode="json"
        )
        app.run()

        self.assertTrue(
            any(
                button.key == "generate-itinerary-narrative"
                for button in app.button
            )
        )
        self.assertTrue(
            any(
                markdown.value
                == "**Why it fits:** It reflects the group's preferences."
                for markdown in app.markdown
            )
        )
        self.assertTrue(
            any(
                text_area.label == "Optional feedback comment"
                for text_area in app.text_area
            )
        )

    def test_trip_story_shows_a_friendly_api_error(self) -> None:
        app = self._sample_results_app()
        app.button(key="build-itinerary").click().run()

        with patch(
            "src.ui.generate_itinerary_narrative",
            side_effect=NarrationGenerationError(
                "OpenAI quota or rate limit reached."
            ),
        ):
            app.button(key="generate-itinerary-narrative").click().run()

        self.assertEqual(
            app.session_state["itinerary_narration_error"],
            "OpenAI quota or rate limit reached.",
        )

    def test_rejected_activity_moves_to_history_and_is_replaced(self) -> None:
        app = self._sample_results_app()
        app.button(key="build-itinerary").click().run()
        before = ItineraryPlan.model_validate(
            app.session_state["itinerary_plan"]
        )
        unchanged_days = {
            day.day_number: day.model_dump(mode="json")
            for day in before.days
            if day.day_number != 1
        }

        app.selectbox(
            key="rejection-reason-rome_colosseum"
        ).set_value("Too much walking")
        app.button(key="confirm-reject-rome_colosseum").click().run()

        after = ItineraryPlan.model_validate(
            app.session_state["itinerary_plan"]
        )
        self.assertIn(
            "rome_colosseum",
            app.session_state["rejected_activities"],
        )
        self.assertNotIn(
            "rome_colosseum",
            [
                activity.activity_id
                for day in after.days
                for activity in day.activities
            ],
        )
        self.assertEqual(
            {
                day.day_number: day.model_dump(mode="json")
                for day in after.days
                if day.day_number != 1
            },
            unchanged_days,
        )
        results_view = next(
            group
            for group in app.get("button_group")
            if group.key == "results_view"
        )
        self.assertIn("Rejected (1)", results_view.options)
        self.assertIn("Must-dos (1)", results_view.options)

    def test_rejected_activity_can_be_restored_to_shortlist(self) -> None:
        app = self._sample_results_app()
        app.button(key="build-itinerary").click().run()
        app.button(key="confirm-reject-rome_colosseum").click().run()
        results_view = next(
            group
            for group in app.get("button_group")
            if group.key == "results_view"
        )
        results_view.select("Rejected (1)").run()
        self.assertTrue(
            any(
                "Rejected: Too expensive" in markdown.value
                for markdown in app.markdown
            )
        )

        app.button(key="restore-rejected-rome_colosseum").click().run()

        self.assertNotIn(
            "rome_colosseum",
            app.session_state["rejected_activities"],
        )
        self.assertIn(
            "rome_colosseum",
            app.session_state["selected_activity_ids"],
        )
        self.assertNotIn(
            "rome_colosseum",
            app.session_state["dismissed_must_do_ids"],
        )
        self.assertIsNone(app.session_state["itinerary_plan"])

    def test_last_replacement_can_be_undone(self) -> None:
        app = self._sample_results_app()
        app.button(key="build-itinerary").click().run()
        before = ItineraryPlan.model_validate(
            app.session_state["itinerary_plan"]
        ).model_dump(mode="json")
        app.button(key="confirm-reject-rome_colosseum").click().run()

        app.button(key="undo-itinerary-replacement").click().run()

        self.assertFalse(app.session_state["rejected_activities"])
        self.assertEqual(
            ItineraryPlan.model_validate(
                app.session_state["itinerary_plan"]
            ).model_dump(mode="json"),
            before,
        )
        self.assertIn(
            "rome_colosseum",
            app.session_state["selected_activity_ids"],
        )


if __name__ == "__main__":
    unittest.main()
