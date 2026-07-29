"""Tests for deterministic activity text retrieval."""

from __future__ import annotations

import unittest

from src.models import Activity, TravelerProfile, TripRequest
from src.search import normalize_search_text, retrieve_activities


def make_activity(
    activity_id: str,
    name: str,
    *,
    city: str = "Rome",
    country: str = "Italy",
    category: str = "museum",
    interests: list[str] | None = None,
    description: str = "A grounded activity fixture.",
) -> Activity:
    return Activity(
        id=activity_id,
        name=name,
        city=city,
        country=country,
        category=category,
        interests=interests or ["history"],
        walking_level="low",
        budget_level="moderate",
        duration_hours=2,
        indoor=True,
        family_friendly=True,
        accessibility_notes="Step-free entrance is available.",
        reservation_required=False,
        description=description,
        source_url="https://example.com/activity",
    )


def make_trip(
    *,
    interests: list[str],
    must_dos: list[str] | None = None,
    destination: str = "Rome",
    country: str = "Italy",
) -> TripRequest:
    return TripRequest(
        destination=destination,
        country=country,
        days=3,
        budget_level="moderate",
        pace="balanced",
        travelers=[
            TravelerProfile(
                name="Coco",
                interests=interests,
                walking_tolerance="moderate",
                must_do_activities=must_dos or [],
            ),
            TravelerProfile(
                name="Sam",
                interests=["food"],
                walking_tolerance="low",
            ),
        ],
    )


class TextRetrievalTests(unittest.TestCase):
    def test_normalization_ignores_case_and_punctuation(self) -> None:
        self.assertEqual(
            normalize_search_text("Ancient-Rome & ART"),
            "ancient rome art",
        )

    def test_retrieves_activity_matching_group_text(self) -> None:
        art_museum = make_activity(
            "rome_art_museum",
            "Art Museum",
            interests=["art", "history"],
        )
        sports_arena = make_activity(
            "rome_sports_arena",
            "Sports Arena",
            category="sports",
            interests=["sports"],
        )

        response = retrieve_activities(
            [sports_arena, art_museum],
            make_trip(interests=["Art"]),
        )

        self.assertEqual(
            [result.activity_id for result in response.results],
            ["rome_art_museum"],
        )
        self.assertEqual(response.results[0].matched_terms, ("art",))
        self.assertIn("Coco", response.results[0].matched_travelers)

    def test_wrong_destination_is_never_retrieved(self) -> None:
        rome_museum = make_activity(
            "rome_art_museum",
            "Rome Art Museum",
            interests=["art"],
        )
        paris_museum = make_activity(
            "paris_art_museum",
            "Paris Art Museum",
            city="Paris",
            country="France",
            interests=["art"],
        )

        response = retrieve_activities(
            [paris_museum, rome_museum],
            make_trip(interests=["art"]),
        )

        self.assertEqual(
            response.destination_activity_ids,
            ("rome_art_museum",),
        )
        self.assertEqual(
            [result.activity_id for result in response.results],
            ["rome_art_museum"],
        )

    def test_compact_must_do_is_included_without_interest_match(self) -> None:
        vatican = make_activity(
            "rome_vatican_museums",
            "Vatican Museums",
            interests=["religion"],
        )
        food_market = make_activity(
            "rome_food_market",
            "Food Market",
            category="food market",
            interests=["food"],
        )

        response = retrieve_activities(
            [food_market, vatican],
            make_trip(
                interests=["cycling"],
                must_dos=["vaticanmuseums"],
            ),
        )

        by_id = {
            result.activity_id: result for result in response.results
        }
        self.assertIn("rome_vatican_museums", by_id)
        self.assertEqual(
            by_id["rome_vatican_museums"].must_do_owners,
            ("Coco",),
        )

    def test_limit_never_discards_recognized_must_dos(self) -> None:
        activities = [
            make_activity(
                "rome_first_must_do",
                "First Must Do",
                interests=["architecture"],
            ),
            make_activity(
                "rome_second_must_do",
                "Second Must Do",
                interests=["photography"],
            ),
            make_activity(
                "rome_food_market",
                "Food Market",
                interests=["food"],
            ),
        ]
        trip = make_trip(
            interests=["art"],
            must_dos=["First Must Do", "second-must-do"],
        )

        response = retrieve_activities(activities, trip, limit=1)

        self.assertEqual(len(response.results), 2)
        self.assertTrue(
            all(result.must_do_owners for result in response.results)
        )

    def test_fallback_returns_destination_catalog_without_duplicates(self) -> None:
        museum = make_activity(
            "rome_museum",
            "Rome Museum",
            interests=["history"],
        )
        response = retrieve_activities(
            [museum, museum],
            make_trip(interests=["surfing"]),
        )

        self.assertTrue(response.used_fallback)
        self.assertEqual(response.destination_activity_ids, ("rome_museum",))
        self.assertEqual(
            [result.activity_id for result in response.results],
            ["rome_museum"],
        )

    def test_empty_destination_returns_no_candidates(self) -> None:
        response = retrieve_activities(
            [
                make_activity(
                    "rome_art_museum",
                    "Rome Art Museum",
                    interests=["art"],
                )
            ],
            make_trip(
                interests=["art"],
                destination="Tokyo",
                country="Japan",
            ),
        )

        self.assertFalse(response.results)
        self.assertFalse(response.destination_activity_ids)
        self.assertFalse(response.used_fallback)


if __name__ == "__main__":
    unittest.main()
