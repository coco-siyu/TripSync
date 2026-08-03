"""Tests for the review-only dlt catalog ingestion workflow."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from datetime import date

from src.catalog_dlt import candidate_records, ingest_cities, ingest_destinations, rotate_destinations
from src.destination_queue import DestinationQueueItem, load_destination_queue
from src.catalog_import import CatalogCandidate, supported_city


class CatalogDltTests(unittest.TestCase):
    def test_candidate_records_keep_provenance_and_pending_status(self) -> None:
        city = supported_city("Rome")
        candidate = CatalogCandidate(
            wikidata_id="Q123",
            name="Example museum",
            latitude=41.9,
            longitude=12.5,
            source_url="https://www.wikidata.org/wiki/Q123",
            wikidata_types=("museum",),
            review_flags=(),
        )
        records = candidate_records(
            city,
            [candidate],
            Path("/Users/cocochen/Downloads/TripSync/data/candidates/rome_wikidata_candidates.json"),
            retrieved_at="2026-08-03T00:00:00+00:00",
        )
        self.assertEqual(records[0]["status"], "pending_human_review")
        self.assertEqual(records[0]["city"], "Rome")
        self.assertEqual(records[0]["candidate_batch"], "data/candidates/rome_wikidata_candidates.json")

    @patch("src.catalog_dlt.write_candidate_file")
    @patch("src.catalog_dlt.resolve_city")
    @patch("src.catalog_dlt.dlt.pipeline")
    def test_ingestion_writes_review_batch_and_appends_raw_records(
        self,
        mock_pipeline_factory: MagicMock,
        mock_resolve_city: MagicMock,
        mock_write_candidate_file: MagicMock,
    ) -> None:
        city = supported_city("Rome")
        mock_resolve_city.return_value = city
        mock_write_candidate_file.return_value = Path("/Users/cocochen/Downloads/TripSync/data/candidates/rome_wikidata_candidates.json")
        pipeline = mock_pipeline_factory.return_value
        candidate = CatalogCandidate("Q123", "Example museum", 41.9, 12.5, "https://example.test", ("museum",), ())

        with tempfile.TemporaryDirectory() as directory:
            summary = ingest_cities(
                ["Rome"], "Italy", candidate_directory=Path(directory),
                destination_path=Path(directory) / "ingestion.duckdb", fetcher=lambda _: [candidate],
            )

        self.assertEqual(summary, {"Rome": 1})
        mock_write_candidate_file.assert_called_once()
        pipeline.run.assert_called_once()
        self.assertEqual(pipeline.run.call_args.kwargs["table_name"], "candidate_runs")

    def test_ingestion_requires_a_city(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one destination"):
            ingest_cities([], "Italy")

    def test_destination_queue_deduplicates_active_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queue.json"
            path.write_text(
                '{"schema_version": 1, "destinations": ['
                '{"city": "Rome", "country": "Italy", "active": true}, '
                '{"city": "rome", "country": "italy", "active": true}, '
                '{"city": "Naples", "country": "Italy", "active": false}]}',
                encoding="utf-8",
            )
            queue = load_destination_queue(path)
        self.assertEqual([(item.city, item.country) for item in queue], [("Rome", "Italy")])

    def test_rotation_selects_a_fair_wrapping_weekly_slice(self) -> None:
        destinations = [
            DestinationQueueItem(city=city, country="Example")
            for city in ("One", "Two", "Three", "Four", "Five")
        ]
        selection = rotate_destinations(destinations, 2, run_date=date(2026, 8, 3))
        self.assertEqual([item.city for item in selection], ["One", "Two"])
        next_week = rotate_destinations(destinations, 2, run_date=date(2026, 8, 10))
        self.assertEqual([item.city for item in next_week], ["Three", "Four"])
        all_destinations = rotate_destinations(destinations, 0, run_date=date(2026, 8, 3))
        self.assertEqual([item.city for item in all_destinations], ["One", "Two", "Three", "Four", "Five"])

    @patch("src.catalog_dlt.dlt.pipeline")
    @patch("src.catalog_dlt.resolve_city")
    @patch("src.catalog_dlt.write_candidate_file")
    def test_destination_queue_items_keep_their_own_countries(
        self,
        mock_write_candidate_file: MagicMock,
        mock_resolve_city: MagicMock,
        mock_pipeline_factory: MagicMock,
    ) -> None:
        mock_resolve_city.side_effect = [supported_city("Rome"), supported_city("Florence")]
        mock_write_candidate_file.return_value = Path("/Users/cocochen/Downloads/TripSync/data/candidates/test.json")
        with tempfile.TemporaryDirectory() as directory:
            ingest_destinations(
                [DestinationQueueItem(city="Rome", country="Italy"), DestinationQueueItem(city="Paris", country="France")],
                candidate_directory=Path(directory), destination_path=Path(directory) / "ingestion.duckdb", fetcher=lambda _: [],
            )
        self.assertEqual(mock_resolve_city.call_args_list[0].args, ("Rome", "Italy"))
        self.assertEqual(mock_resolve_city.call_args_list[1].args, ("Paris", "France"))


if __name__ == "__main__":
    unittest.main()
