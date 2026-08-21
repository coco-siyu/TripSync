"""Tests for local saved-trip persistence."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.models import TripRequest
from src.trips import (
    SavedTrip,
    itinerary_versions,
    list_saved_trips,
    save_trip,
    state_for_itinerary_version,
)
from src.trips_ui import itinerary_comparison_for_versions, itinerary_plan_for_version


class SavedTripsTests(unittest.TestCase):
    def test_saves_and_lists_validated_trip(self) -> None:
        trip = TripRequest.model_validate({"destination":"Rome", "country":"Italy", "days":2, "budget_level":"moderate", "pace":"balanced", "travelers":[{"name":"A", "interests":["art"], "walking_tolerance":"low"},{"name":"B", "interests":["history"], "walking_tolerance":"moderate"}]})
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "trips.db"
            with (
                patch("src.trips.DEFAULT_FEEDBACK_DATABASE_PATH", database),
                patch("src.trips.is_configured", return_value=False),
            ):
                saved = save_trip(trip, {"selected_activity_ids": ["rome_pantheon"]})
                records = list_saved_trips()
        self.assertEqual(records[0].trip, trip)
        self.assertEqual(records[0].trip_id, saved.trip_id)

    def test_save_with_existing_id_updates_a_trip(self) -> None:
        trip = TripRequest.model_validate({"destination":"Rome", "country":"Italy", "days":2, "budget_level":"moderate", "pace":"balanced", "travelers":[{"name":"A", "interests":["art"], "walking_tolerance":"low"},{"name":"B", "interests":["history"], "walking_tolerance":"moderate"}]})
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "trips.db"
            with (
                patch("src.trips.DEFAULT_FEEDBACK_DATABASE_PATH", database),
                patch("src.trips.is_configured", return_value=False),
            ):
                saved = save_trip(trip, {"selected_activity_ids": ["rome_pantheon"]})
                updated = save_trip(
                    trip,
                    {"selected_activity_ids": ["rome_pantheon", "rome_forum"]},
                    trip_id=saved.trip_id,
                )
                records = list_saved_trips()
        self.assertEqual(updated.trip_id, saved.trip_id)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].state["selected_activity_ids"], ["rome_pantheon", "rome_forum"])

    def test_saving_new_itineraries_keeps_prior_versions(self) -> None:
        trip = TripRequest.model_validate({"destination":"Rome", "country":"Italy", "days":2, "budget_level":"moderate", "pace":"balanced", "travelers":[{"name":"A", "interests":["art"], "walking_tolerance":"low"},{"name":"B", "interests":["history"], "walking_tolerance":"moderate"}]})
        first_state = {"selected_activity_ids": ["rome_pantheon"], "itinerary_plan": {"days": [{"day_number": 1}]}}
        second_state = {"selected_activity_ids": ["rome_forum"], "itinerary_plan": {"days": [{"day_number": 2}]}}
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "trips.db"
            with (
                patch("src.trips.DEFAULT_FEEDBACK_DATABASE_PATH", database),
                patch("src.trips.is_configured", return_value=False),
            ):
                saved = save_trip(trip, first_state, save_itinerary_version=True)
                save_trip(trip, second_state, trip_id=saved.trip_id, save_itinerary_version=True)
                record = list_saved_trips()[0]
        versions = itinerary_versions(record.state, fallback_updated_at=record.updated_at)
        self.assertEqual(len(versions), 2)
        first_restored = state_for_itinerary_version(record.state, versions[0]["version_id"])
        self.assertEqual(first_restored["selected_activity_ids"], ["rome_pantheon"])
        self.assertEqual(record.state["selected_activity_ids"], ["rome_forum"])

    def test_reads_the_selected_saved_itinerary_version(self) -> None:
        trip = TripRequest.model_validate({"destination":"Rome", "country":"Italy", "days":2, "budget_level":"moderate", "pace":"balanced", "travelers":[{"name":"A", "interests":["art"], "walking_tolerance":"low"},{"name":"B", "interests":["history"], "walking_tolerance":"moderate"}]})
        plan = {
            "destination": "Rome",
            "country": "Italy",
            "pace": "balanced",
            "auto_fill": True,
            "days": [
                {
                    "day_number": 1,
                    "activities": [
                        {
                            "activity_id": "rome_pantheon",
                            "activity_name": "Pantheon",
                            "duration_hours": 1.0,
                            "source": "shortlist",
                            "must_do_owners": [],
                            "traveler_names": ["A"],
                            "reason": "A saved choice.",
                        }
                    ],
                    "activity_hours": 1.0,
                    "transition_hours": 0.0,
                    "planned_hours": 1.0,
                    "capacity_hours": 6.0,
                    "pace_override_approved": False,
                }
            ],
            "unscheduled": [],
        }
        record = SavedTrip(
            trip_id="saved-rome",
            title="Rome · 2 days",
            trip=trip,
            state={
                "itinerary_versions": [
                    {
                        "version_id": "version-one",
                        "label": "Itinerary 1",
                        "saved_at": "2026-08-19T19:00:00+00:00",
                        "itinerary_plan": plan,
                    }
                ]
            },
            updated_at="2026-08-19T19:00:00+00:00",
        )

        restored = itinerary_plan_for_version(record, "version-one")

        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(restored.days[0].activities[0].activity_id, "rome_pantheon")

    def test_compares_saved_itinerary_versions(self) -> None:
        trip = TripRequest.model_validate({"destination":"Rome", "country":"Italy", "days":2, "budget_level":"moderate", "pace":"balanced", "travelers":[{"name":"A", "interests":["art"], "walking_tolerance":"low"},{"name":"B", "interests":["history"], "walking_tolerance":"moderate"}]})

        def plan(activity_id: str, activity_name: str) -> dict:
            return {
                "destination": "Rome", "country": "Italy", "pace": "balanced", "auto_fill": True,
                "days": [{"day_number": 1, "activities": [{"activity_id": activity_id, "activity_name": activity_name, "duration_hours": 1.0, "source": "shortlist", "must_do_owners": [], "traveler_names": ["A"], "reason": "A saved choice."}], "activity_hours": 1.0, "transition_hours": 0.5, "planned_hours": 1.5, "capacity_hours": 6.0, "pace_override_approved": False}],
                "unscheduled": [],
            }

        record = SavedTrip(
            trip_id="saved-rome",
            title="Rome · 2 days",
            trip=trip,
            state={"itinerary_versions": [
                {"version_id": "first", "itinerary_plan": plan("rome_pantheon", "Pantheon")},
                {"version_id": "second", "itinerary_plan": plan("rome_forum", "Roman Forum")},
            ]},
            updated_at="2026-08-19T19:00:00+00:00",
        )

        comparison = itinerary_comparison_for_versions(record, "first", "second")

        self.assertIsNotNone(comparison)
        assert comparison is not None
        first, second = comparison
        self.assertEqual(first["activity_count"], 1)
        self.assertEqual(first["only_here"], ["Pantheon"])
        self.assertEqual(second["only_here"], ["Roman Forum"])
