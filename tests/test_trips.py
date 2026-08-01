"""Tests for local saved-trip persistence."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.models import TripRequest
from src.trips import list_saved_trips, save_trip


class SavedTripsTests(unittest.TestCase):
    def test_saves_and_lists_validated_trip(self) -> None:
        trip = TripRequest.model_validate({"destination":"Rome", "country":"Italy", "days":2, "budget_level":"moderate", "pace":"balanced", "travelers":[{"name":"A", "interests":["art"], "walking_tolerance":"low"},{"name":"B", "interests":["history"], "walking_tolerance":"moderate"}]})
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "trips.db"
            with patch("src.trips.DEFAULT_FEEDBACK_DATABASE_PATH", database):
                saved = save_trip(trip, {"selected_activity_ids": ["rome_pantheon"]})
                records = list_saved_trips()
        self.assertEqual(records[0].trip, trip)
        self.assertEqual(records[0].trip_id, saved.trip_id)

    def test_save_with_existing_id_updates_a_trip(self) -> None:
        trip = TripRequest.model_validate({"destination":"Rome", "country":"Italy", "days":2, "budget_level":"moderate", "pace":"balanced", "travelers":[{"name":"A", "interests":["art"], "walking_tolerance":"low"},{"name":"B", "interests":["history"], "walking_tolerance":"moderate"}]})
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "trips.db"
            with patch("src.trips.DEFAULT_FEEDBACK_DATABASE_PATH", database):
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
