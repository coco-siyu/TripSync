"""Tests for local saved-trip persistence."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from src.models import ItineraryPlan, TripRequest
from src.trips import (
    SavedTrip,
    claim_anonymous_trips,
    itinerary_versions,
    list_saved_trips,
    revise_itinerary_plan,
    save_trip,
    state_for_itinerary_version,
)
from src.trips_ui import itinerary_comparison_for_versions, itinerary_plan_for_version


class SavedTripsTests(unittest.TestCase):
    def test_lists_rls_visible_owned_trips(self) -> None:
        trip = TripRequest.model_validate({"destination":"Rome", "country":"Italy", "days":2, "budget_level":"moderate", "pace":"balanced", "travelers":[{"name":"A", "interests":["art"], "walking_tolerance":"low"},{"name":"B", "interests":["history"], "walking_tolerance":"moderate"}]})
        row = {
            "session_id": "owner-user",
            "trip_id": "owned-trip",
            "title": "Rome plan",
            "trip_json": trip.model_dump(mode="json"),
            "state_json": {},
            "updated_at": "2026-08-23T12:00:00+00:00",
        }
        with (
            patch("src.trips.is_configured", return_value=True),
            patch("src.trips.select_authenticated", return_value=[row]) as select_mock,
        ):
            records = list_saved_trips(
                "owner-user",
                auth_access_token="access-token",
            )

        self.assertEqual(records[0].owner_id, "owner-user")
        select_mock.assert_called_once_with(
            "saved_trips",
            "access-token",
            order="updated_at",
        )

    def test_claims_a_valid_anonymous_browser_namespace_via_authenticated_rpc(self) -> None:
        anonymous_session_id = "a" * 32
        with patch("src.trips.rpc_authenticated", return_value=2) as rpc_mock:
            claimed_count = claim_anonymous_trips(
                anonymous_session_id,
                "access-token",
            )

        self.assertEqual(claimed_count, 2)
        rpc_mock.assert_called_once_with(
            "claim_anonymous_trips",
            {"anonymous_session_id": anonymous_session_id},
            "access-token",
        )

    def test_rejects_account_style_or_malformed_transfer_namespaces(self) -> None:
        with (
            patch("src.trips.rpc_authenticated") as rpc_mock,
            self.assertRaisesRegex(ValueError, "not valid"),
        ):
            claim_anonymous_trips(
                "12345678-1234-1234-1234-123456789012",
                "access-token",
            )

        rpc_mock.assert_not_called()

    def test_signed_in_user_creates_an_rls_owned_trip(self) -> None:
        trip = TripRequest.model_validate({"destination":"Rome", "country":"Italy", "days":2, "budget_level":"moderate", "pace":"balanced", "travelers":[{"name":"A", "interests":["art"], "walking_tolerance":"low"},{"name":"B", "interests":["history"], "walking_tolerance":"moderate"}]})
        with (
            patch("src.trips.is_configured", return_value=True),
            patch("src.trips.select_authenticated", return_value=[]),
            patch("src.trips.insert_authenticated") as insert_mock,
            patch("src.trips.upsert") as service_upsert_mock,
        ):
            saved = save_trip(
                trip,
                {},
                session_id="user-123",
                auth_access_token="access-token",
                owner_id="a" * 32,
            )

        self.assertEqual(saved.owner_id, "user-123")
        self.assertEqual(insert_mock.call_args.args[1]["session_id"], "user-123")
        service_upsert_mock.assert_not_called()

    def test_signed_in_update_keeps_database_ownership_over_stale_ui_state(self) -> None:
        trip = TripRequest.model_validate({"destination":"Rome", "country":"Italy", "days":2, "budget_level":"moderate", "pace":"balanced", "travelers":[{"name":"A", "interests":["art"], "walking_tolerance":"low"},{"name":"B", "interests":["history"], "walking_tolerance":"moderate"}]})
        shared = SavedTrip(
            "owned-trip",
            "Rome plan",
            trip,
            {},
            "2026-08-23T12:00:00+00:00",
            "owner-user",
        )
        with (
            patch("src.trips.is_configured", return_value=True),
            patch("src.trips.list_saved_trips", return_value=[shared]),
            patch("src.trips.update_authenticated") as update_mock,
        ):
            saved = save_trip(
                trip,
                {"selected_activity_ids": ["rome_pantheon"]},
                trip_id="owned-trip",
                session_id="owner-user",
                auth_access_token="access-token",
                owner_id="a" * 32,
            )

        self.assertEqual(saved.owner_id, "owner-user")
        update_row = update_mock.call_args.args[1]
        self.assertNotIn("session_id", update_row)
        self.assertNotIn("trip_id", update_row)
        self.assertEqual(update_mock.call_args.kwargs["filters"], {"trip_id": "owned-trip"})

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

    def test_local_saved_trips_are_isolated_by_browser_session(self) -> None:
        trip = TripRequest.model_validate({"destination":"Rome", "country":"Italy", "days":2, "budget_level":"moderate", "pace":"balanced", "travelers":[{"name":"A", "interests":["art"], "walking_tolerance":"low"},{"name":"B", "interests":["history"], "walking_tolerance":"moderate"}]})
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "trips.db"
            with (
                patch("src.trips.DEFAULT_FEEDBACK_DATABASE_PATH", database),
                patch("src.trips.is_configured", return_value=False),
            ):
                save_trip(trip, {}, session_id="browser-one")
                first_records = list_saved_trips("browser-one")
                second_records = list_saved_trips("browser-two")

        self.assertEqual(len(first_records), 1)
        self.assertEqual(second_records, [])

    def test_legacy_local_rows_move_to_the_first_current_browser_session(self) -> None:
        trip = TripRequest.model_validate({"destination":"Rome", "country":"Italy", "days":2, "budget_level":"moderate", "pace":"balanced", "travelers":[{"name":"A", "interests":["art"], "walking_tolerance":"low"},{"name":"B", "interests":["history"], "walking_tolerance":"moderate"}]})
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "trips.db"
            with closing(sqlite3.connect(database)) as connection:
                with connection:
                    connection.execute("""CREATE TABLE saved_trips (
                        trip_id TEXT PRIMARY KEY, title TEXT NOT NULL,
                        trip_json TEXT NOT NULL, state_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL)""")
                    connection.execute(
                        """INSERT INTO saved_trips (
                            trip_id, title, trip_json, state_json, updated_at
                        ) VALUES (?, ?, ?, ?, ?)""",
                        (
                            "legacy-trip",
                            "Legacy Rome",
                            json.dumps(trip.model_dump(mode="json")),
                            json.dumps({"selected_activity_ids": ["rome_pantheon"]}),
                            "2026-08-23T12:00:00+00:00",
                        ),
                    )
            with (
                patch("src.trips.DEFAULT_FEEDBACK_DATABASE_PATH", database),
                patch("src.trips.is_configured", return_value=False),
            ):
                records = list_saved_trips("browser-one")
                other_records = list_saved_trips("browser-two")

        self.assertEqual([record.trip_id for record in records], ["legacy-trip"])
        self.assertEqual(records[0].owner_id, "browser-one")
        self.assertEqual(other_records, [])

    def test_saving_new_itineraries_keeps_prior_versions(self) -> None:
        trip = TripRequest.model_validate({"destination":"Rome", "country":"Italy", "days":2, "budget_level":"moderate", "pace":"balanced", "travelers":[{"name":"A", "interests":["art"], "walking_tolerance":"low"},{"name":"B", "interests":["history"], "walking_tolerance":"moderate"}]})
        first_state = {"selected_activity_ids": ["rome_pantheon"], "itinerary_plan": {"days": [{"day_number": 1}]}}
        second_state = {
            "selected_activity_ids": ["rome_forum"],
            "auto_select_must_dos": False,
            "itinerary_plan": {"days": [{"day_number": 2}]},
        }
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
        self.assertTrue(first_restored["auto_select_must_dos"])
        self.assertEqual(record.state["selected_activity_ids"], ["rome_forum"])
        self.assertFalse(record.state["auto_select_must_dos"])

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

    def test_revising_itinerary_requires_explicit_capacity_override(self) -> None:
        plan = ItineraryPlan.model_validate(
            {
                "destination": "Rome",
                "country": "Italy",
                "pace": "balanced",
                "auto_fill": True,
                "days": [
                    {
                        "day_number": 1,
                        "activities": [
                            {
                                "activity_id": "one",
                                "activity_name": "One",
                                "duration_hours": 1.0,
                                "source": "shortlist",
                                "must_do_owners": [],
                                "traveler_names": ["A"],
                                "reason": "Saved choice.",
                            }
                        ],
                        "activity_hours": 1.0,
                        "transition_hours": 0.0,
                        "planned_hours": 1.0,
                        "capacity_hours": 1.0,
                        "pace_override_approved": False,
                    },
                    {
                        "day_number": 2,
                        "activities": [
                            {
                                "activity_id": "two",
                                "activity_name": "Two",
                                "duration_hours": 1.0,
                                "source": "shortlist",
                                "must_do_owners": [],
                                "traveler_names": ["A"],
                                "reason": "Saved choice.",
                            }
                        ],
                        "activity_hours": 1.0,
                        "transition_hours": 0.0,
                        "planned_hours": 1.0,
                        "capacity_hours": 6.0,
                        "pace_override_approved": False,
                    },
                ],
                "unscheduled": [],
            }
        )

        with self.assertRaisesRegex(ValueError, "Confirm the pace override"):
            revise_itinerary_plan(plan, target_day_by_activity_id={"two": 1})

        revised = revise_itinerary_plan(
            plan,
            target_day_by_activity_id={"two": 1},
            allow_pace_override=True,
        )
        self.assertEqual([item.activity_id for item in revised.days[0].activities], ["one", "two"])
        self.assertEqual(revised.days[0].planned_hours, 2.5)
        self.assertTrue(revised.days[0].pace_override_approved)
        self.assertEqual(revised.days[1].activities, [])

    def test_revising_itinerary_enforces_pace_activity_limit(self) -> None:
        activity = {
            "duration_hours": 0.5,
            "source": "shortlist",
            "must_do_owners": [],
            "traveler_names": ["A"],
            "reason": "Saved choice.",
        }
        plan = ItineraryPlan.model_validate(
            {
                "destination": "Rome",
                "country": "Italy",
                "pace": "relaxed",
                "auto_fill": True,
                "days": [
                    {
                        "day_number": 1,
                        "activities": [
                            {
                                **activity,
                                "activity_id": "one",
                                "activity_name": "One",
                            },
                            {
                                **activity,
                                "activity_id": "two",
                                "activity_name": "Two",
                            },
                        ],
                        "activity_hours": 1.0,
                        "transition_hours": 0.5,
                        "planned_hours": 1.5,
                        "capacity_hours": 4.0,
                        "pace_override_approved": False,
                    },
                    {
                        "day_number": 2,
                        "activities": [
                            {
                                **activity,
                                "activity_id": "three",
                                "activity_name": "Three",
                            }
                        ],
                        "activity_hours": 0.5,
                        "transition_hours": 0.0,
                        "planned_hours": 0.5,
                        "capacity_hours": 4.0,
                        "pace_override_approved": False,
                    },
                ],
                "unscheduled": [],
            }
        )

        with self.assertRaisesRegex(ValueError, "more than 2 activities"):
            revise_itinerary_plan(
                plan,
                target_day_by_activity_id={"three": 1},
            )

        revised = revise_itinerary_plan(
            plan,
            target_day_by_activity_id={"three": 1},
            allow_pace_override=True,
        )
        self.assertTrue(revised.days[0].pace_override_approved)

    def test_named_alternative_preserves_source_snapshot(self) -> None:
        trip = TripRequest.model_validate({"destination":"Rome", "country":"Italy", "days":2, "budget_level":"moderate", "pace":"balanced", "travelers":[{"name":"A", "interests":["art"], "walking_tolerance":"low"},{"name":"B", "interests":["history"], "walking_tolerance":"moderate"}]})
        initial_state = {
            "selected_activity_ids": ["rome_pantheon"],
            "itinerary_plan": {"days": [{"day_number": 1}]},
        }
        alternative_state = {
            "selected_activity_ids": ["rome_forum"],
            "itinerary_plan": {"days": [{"day_number": 1}]},
        }
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "trips.db"
            with (
                patch("src.trips.DEFAULT_FEEDBACK_DATABASE_PATH", database),
                patch("src.trips.is_configured", return_value=False),
            ):
                saved = save_trip(trip, initial_state, save_itinerary_version=True)
                updated = save_trip(
                    trip,
                    alternative_state,
                    trip_id=saved.trip_id,
                    save_itinerary_version=True,
                    itinerary_label="Museum-focused alternative",
                    force_new_itinerary_version=True,
                )

        versions = itinerary_versions(updated.state, fallback_updated_at=updated.updated_at)
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0]["label"], "Itinerary 1")
        self.assertEqual(versions[1]["label"], "Museum-focused alternative")
        self.assertEqual(
            state_for_itinerary_version(updated.state, versions[0]["version_id"])["selected_activity_ids"],
            ["rome_pantheon"],
        )
        self.assertEqual(
            state_for_itinerary_version(updated.state, versions[1]["version_id"])["selected_activity_ids"],
            ["rome_forum"],
        )
