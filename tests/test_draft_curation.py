"""Tests for the deterministic catalog quality gate."""

from __future__ import annotations

import unittest

from src.catalog import auto_curate_candidates, review_curate_candidates
from src.draft_curation import classify_candidate


def candidate(name: str, *types: str, wikipedia_url: str | None = None) -> dict:
    return {
        "wikidata_id": "Q123",
        "name": name,
        "source_url": "https://www.wikidata.org/wiki/Q123",
        "wikidata_types": list(types),
        "wikipedia_url": wikipedia_url,
    }


class CandidateQualityGateTests(unittest.TestCase):
    def test_rejects_clear_non_visitor_records(self) -> None:
        examples = [
            candidate("Example Hotel", "hotel"),
            candidate("Example Airport", "airport"),
            candidate("November 2015 Paris attacks", "terrorist attack"),
            candidate("Organisation internationale", "international organization"),
            candidate("Q55415207", "church building"),
        ]
        self.assertEqual(
            [classify_candidate(item).outcome for item in examples],
            ["reject"] * len(examples),
        )

    def test_marks_clear_attractions_ready_for_explicit_publish(self) -> None:
        examples = [
            candidate("Doge's Palace", "palace", wikipedia_url="https://en.wikipedia.org/wiki/Doge%27s_Palace"),
            candidate("Uffizi Gallery", "art museum"),
        ]
        decisions = [classify_candidate(item) for item in examples]
        self.assertEqual([decision.outcome for decision in decisions], ["auto_publish", "auto_publish"])
        self.assertGreater(decisions[0].confidence, decisions[1].confidence)

    def test_promotes_well_evidenced_broad_london_attraction_types(self) -> None:
        examples = [
            candidate(
                "Tower of London",
                "Grade I listed building",
                "tourist attraction",
                wikipedia_url="https://en.wikipedia.org/wiki/Tower_of_London",
            ),
            candidate(
                "Hampton Court Palace",
                "historic house",
                wikipedia_url="https://en.wikipedia.org/wiki/Hampton_Court_Palace",
            ),
        ]
        self.assertEqual(
            [classify_candidate(item).outcome for item in examples],
            ["auto_publish", "auto_publish"],
        )

    def test_keeps_broad_types_without_wikipedia_for_review(self) -> None:
        decision = classify_candidate(candidate("Unknown listed building", "listed building"))
        self.assertEqual(decision.outcome, "review")

    def test_keeps_context_dependent_places_for_review(self) -> None:
        examples = [
            candidate("University of Paris", "university"),
            candidate("Biblioteca Nazionale Marciana", "library"),
            candidate("Carnival of Venice", "annual event"),
        ]
        self.assertEqual(
            [classify_candidate(item).outcome for item in examples],
            ["review"] * len(examples),
        )

    def test_auto_and_review_drafts_are_kept_separate(self) -> None:
        museum = candidate("Example Museum", "museum")
        university = candidate("Example University", "university")
        hotel = candidate("Example Hotel", "hotel")

        automatic, auto_skipped = auto_curate_candidates(
            [museum, university, hotel], "Example City", "Example Country"
        )
        review, review_skipped = review_curate_candidates(
            [museum, university, hotel], "Example City", "Example Country"
        )

        self.assertEqual([activity.name for activity in automatic], ["Example Museum"])
        self.assertIn("Example University", auto_skipped)
        self.assertIn("Example Hotel", auto_skipped)
        self.assertEqual([activity.name for activity in review], ["Example University"])
        self.assertIn("Example Museum", review_skipped)
