"""Tests for the review-only Wikidata catalog importer."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from src.catalog_import import (
    CatalogImportError,
    SUPPORTED_CITIES,
    build_query,
    fetch_candidates,
    parse_candidates,
    supported_city,
    write_candidate_file,
)


PAYLOAD = {
    "results": {
        "bindings": [
            {
                "item": {"value": "http://www.wikidata.org/entity/Q123"},
                "itemLabel": {"value": "Example Museum"},
                "coord": {"value": "Point(12.5 41.9)"},
                "typeLabel": {"value": "museum"},
                "article": {"value": "https://en.wikipedia.org/wiki/Example_Museum"},
            },
            {
                "item": {"value": "http://www.wikidata.org/entity/Q123"},
                "itemLabel": {"value": "Example Museum"},
                "coord": {"value": "Point(12.5 41.9)"},
                "typeLabel": {"value": "art museum"},
            },
            {
                "item": {"value": "http://www.wikidata.org/entity/Q456"},
                "itemLabel": {"value": "Example Park"},
                "coord": {"value": "Point(12.6 42.0)"},
                "typeLabel": {"value": "park"},
                "image": {"value": "https://commons.wikimedia.org/example.jpg"},
            },
        ]
    }
}


class CatalogImportTests(unittest.TestCase):
    def test_supports_the_four_initial_italian_cities(self) -> None:
        self.assertEqual(set(SUPPORTED_CITIES), {"rome", "florence", "milan", "venice"})
        self.assertEqual(supported_city(" FLORENCE ").wikidata_id, "Q2044")
        with self.assertRaisesRegex(ValueError, "Unsupported city"):
            supported_city("Naples")

    def test_query_is_bounded_and_uses_the_city_entity(self) -> None:
        query = build_query(supported_city("Rome"), limit=24)
        self.assertIn("wdt:P131* wd:Q220", query)
        self.assertIn("SELECT ?item ?coord ?sitelinks WHERE", query)
        self.assertIn("LIMIT 24", query)
        with self.assertRaises(ValueError):
            build_query(supported_city("Rome"), limit=251)

    def test_parses_and_deduplicates_candidates(self) -> None:
        candidates = parse_candidates(PAYLOAD)
        self.assertEqual([candidate.wikidata_id for candidate in candidates], ["Q123", "Q456"])
        self.assertEqual(candidates[0].latitude, 41.9)
        self.assertEqual(candidates[0].longitude, 12.5)
        self.assertEqual(candidates[0].source_url, "https://www.wikidata.org/wiki/Q123")
        self.assertIsNone(candidates[1].wikipedia_url)
        self.assertEqual(candidates[0].wikidata_types, ("art museum", "museum"))
        self.assertEqual(candidates[0].review_flags, ())

    def test_filters_airports_but_flags_context_dependent_places(self) -> None:
        payload = {
            "results": {
                "bindings": [
                    {
                        "item": {"value": "http://www.wikidata.org/entity/Q1"},
                        "itemLabel": {"value": "Example Airport"},
                        "coord": {"value": "Point(12.5 41.9)"},
                        "typeLabel": {"value": "airport"},
                    },
                    {
                        "item": {"value": "http://www.wikidata.org/entity/Q2"},
                        "itemLabel": {"value": "Example University"},
                        "coord": {"value": "Point(12.6 42.0)"},
                        "typeLabel": {"value": "university"},
                    },
                ]
            }
        }

        candidates = parse_candidates(payload)

        self.assertEqual([candidate.name for candidate in candidates], ["Example University"])
        self.assertIn("verify visitor access", candidates[0].review_flags[0])

    def test_filters_events_and_organisations_before_the_review_batch(self) -> None:
        payload = {
            "results": {
                "bindings": [
                    {
                        "item": {"value": "http://www.wikidata.org/entity/Q1"},
                        "itemLabel": {"value": "November 2015 Paris attacks"},
                        "coord": {"value": "Point(2.3 48.8)"},
                        "typeLabel": {"value": "terrorist attack"},
                    },
                    {
                        "item": {"value": "http://www.wikidata.org/entity/Q2"},
                        "itemLabel": {"value": "Example Organisation"},
                        "coord": {"value": "Point(2.3 48.8)"},
                        "typeLabel": {"value": "international organization"},
                    },
                    {
                        "item": {"value": "http://www.wikidata.org/entity/Q3"},
                        "itemLabel": {"value": "Example Palace"},
                        "coord": {"value": "Point(2.3 48.8)"},
                        "typeLabel": {"value": "palace"},
                    },
                ]
            }
        }

        self.assertEqual([item.name for item in parse_candidates(payload)], ["Example Palace"])

    def test_writes_review_only_document(self) -> None:
        city = supported_city("Rome")
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = write_candidate_file(
                city,
                parse_candidates(PAYLOAD),
                Path(temporary_directory),
            )
            document = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(document["city"], "Rome")
        self.assertEqual(document["source"]["license"], "CC0 for Wikidata structured data")
        self.assertIn("manually reviewed", document["review_note"])
        self.assertEqual(len(document["candidates"]), 2)

    @patch("src.catalog_import.time.sleep")
    @patch("src.catalog_import.urlopen")
    def test_retries_a_temporary_wikidata_error(
        self,
        mock_urlopen: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        response = MagicMock()
        response.__enter__.return_value = response
        mock_urlopen.side_effect = [
            HTTPError("https://example.test", 502, "Bad Gateway", None, None),
            response,
        ]
        with patch("src.catalog_import.json.load", return_value=PAYLOAD):
            candidates = fetch_candidates(supported_city("Florence"), retries=1)

        self.assertEqual(len(candidates), 2)
        self.assertEqual(mock_urlopen.call_count, 2)
        mock_sleep.assert_called_once_with(1)

    @patch("src.catalog_import.urlopen")
    def test_reports_persistent_wikidata_errors_cleanly(
        self,
        mock_urlopen: MagicMock,
    ) -> None:
        mock_urlopen.side_effect = HTTPError(
            "https://example.test", 502, "Bad Gateway", None, None
        )

        with self.assertRaisesRegex(CatalogImportError, "HTTP 502"):
            fetch_candidates(supported_city("Florence"), retries=0)

    @patch("src.catalog_import.time.sleep")
    @patch("src.catalog_import.urlopen")
    def test_retries_a_wikidata_timeout(
        self,
        mock_urlopen: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        response = MagicMock()
        response.__enter__.return_value = response
        mock_urlopen.side_effect = [TimeoutError("slow response"), response]

        with patch("src.catalog_import.json.load", return_value=PAYLOAD):
            candidates = fetch_candidates(supported_city("Florence"), retries=1)

        self.assertEqual(len(candidates), 2)
        self.assertEqual(mock_urlopen.call_count, 2)
        mock_sleep.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
