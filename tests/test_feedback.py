"""Tests for local persistence of feedback on generated LLM output."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.feedback import (
    feedback_target_id,
    list_feedback,
    list_overall_experience_feedback,
    record_feedback,
    record_overall_experience_feedback,
)


class FeedbackTests(unittest.TestCase):
    def test_target_id_is_stable_without_exposing_payload(self) -> None:
        payload = {"title": "Calmer afternoon", "day_number": 2}

        target_id = feedback_target_id("adjustment_proposal", payload)

        self.assertEqual(
            target_id,
            feedback_target_id("adjustment_proposal", payload),
        )
        self.assertNotIn("Calmer afternoon", target_id)

    def test_feedback_is_upserted_per_session_and_generated_item(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "feedback.db"
            target_id = feedback_target_id("trip_story", {"summary": "Rome"})
            record_feedback(
                session_id="anonymous-session",
                target_type="trip_story",
                target_id=target_id,
                rating="up",
                comment="Clear and warm.",
                database_path=database_path,
            )
            record_feedback(
                session_id="anonymous-session",
                target_type="trip_story",
                target_id=target_id,
                rating="down",
                comment="Too generic after rereading.",
                database_path=database_path,
            )

            records = list_feedback(database_path)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].rating, "down")
        self.assertEqual(records[0].comment, "Too generic after rereading.")

    def test_overall_rubric_is_upserted_per_session_and_itinerary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "feedback.db"
            itinerary_id = feedback_target_id(
                "overall_experience", {"destination": "Rome", "days": 3}
            )
            record_overall_experience_feedback(
                session_id="anonymous-session",
                itinerary_id=itinerary_id,
                helpfulness=5,
                clarity=4,
                group_fit=4,
                database_path=database_path,
            )
            record_overall_experience_feedback(
                session_id="anonymous-session",
                itinerary_id=itinerary_id,
                helpfulness=4,
                clarity=5,
                group_fit=5,
                comment="Easy to compare ideas.",
                database_path=database_path,
            )

            records = list_overall_experience_feedback(database_path)

        self.assertEqual(len(records), 1)
        self.assertEqual((records[0].helpfulness, records[0].clarity, records[0].group_fit), (4, 5, 5))
        self.assertEqual(records[0].comment, "Easy to compare ideas.")
