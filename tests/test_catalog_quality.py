"""Tests for official-source coverage reporting."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.catalog_quality import (
    activity_release_issues,
    catalog_quality_rows,
    destination_quality_rows,
    official_source_status,
    summarize_destination_release,
    summarize_catalog_quality,
)
from src.models import Activity


SAMPLE_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "sample_activities.json"


def activity_with(**updates: object) -> Activity:
    raw = json.loads(SAMPLE_DATA_PATH.read_text(encoding="utf-8"))[0]
    raw.update(updates)
    return Activity.model_validate(raw)


class CatalogQualityTests(unittest.TestCase):
    def test_summary_counts_only_verified_official_coverage(self) -> None:
        verified = activity_with(
            official_url="https://museum.example.org",
            official_site_verified=True,
            official_hours_url="https://museum.example.org/hours",
            official_tickets_url="https://museum.example.org/tickets",
        )
        missing = activity_with(id="rome_second_activity", name="Second activity")

        summary = summarize_catalog_quality([verified, missing])

        self.assertEqual(summary.total, 2)
        self.assertEqual(summary.verified_official_sites, 1)
        self.assertEqual(summary.official_hours_links, 1)
        self.assertEqual(summary.official_ticket_links, 1)

    def test_unverified_candidate_is_described_as_awaiting_automation(self) -> None:
        activity = activity_with(official_url="https://museum.example.org")

        self.assertEqual(
            official_source_status(activity),
            "Awaiting automatic verification",
        )
        self.assertEqual(
            catalog_quality_rows([activity])[0]["Official source"],
            "Awaiting automatic verification",
        )

    def test_destination_rows_are_sorted_and_report_percentages(self) -> None:
        verified = activity_with(
            city="Rome",
            country="Italy",
            official_url="https://museum.example.org",
            official_site_verified=True,
        )
        missing = activity_with(
            id="florence_second_activity",
            name="Second activity",
            city="Florence",
            country="Italy",
        )

        rows = destination_quality_rows([verified, missing])

        self.assertEqual(
            [row["Destination"] for row in rows],
            ["Florence, Italy", "Rome, Italy"],
        )
        self.assertEqual(rows[0]["Official sites"], 0.0)
        self.assertEqual(rows[1]["Official sites"], 1.0)

    def test_release_gate_requires_stable_source_identity(self) -> None:
        locator_only = activity_with(source_url="https://en.wikipedia.org/wiki/Example")
        stable_wikidata = activity_with(
            id="rome_wikidata_activity",
            name="Wikidata activity",
            source_url="https://www.wikidata.org/wiki/Q123",
        )
        identified = activity_with(
            id="rome_identified_activity",
            name="Identified activity",
            source_url="https://museum.example.org/visit",
        )

        self.assertEqual(
            activity_release_issues(locator_only),
            ("Reliable source identity missing",),
        )
        self.assertEqual(activity_release_issues(stable_wikidata), ())
        self.assertEqual(activity_release_issues(identified), ())

    def test_release_summary_checks_target_and_diversity(self) -> None:
        activities = [
            activity_with(
                id=f"rome_activity_{index}",
                name=f"Activity {index}",
                category=category,
                budget_level=budget,
                walking_level=walking,
                latitude=41.80 + index * 0.02,
                longitude=12.40 + index * 0.03,
            )
            for index, (category, budget, walking) in enumerate(
                [
                    ("museum", "free", "low"),
                    ("park", "low", "moderate"),
                    ("market", "moderate", "high"),
                    ("landmark", "free", "low"),
                    ("historic_site", "low", "moderate"),
                    ("gallery", "moderate", "high"),
                ]
            )
        ]

        summary = summarize_destination_release(activities, target=6)

        self.assertTrue(summary.ready)
        self.assertEqual(summary.categories, 6)
        self.assertGreaterEqual(summary.location_zones, 4)


if __name__ == "__main__":
    unittest.main()
