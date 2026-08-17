"""Tests for the quality-gated dlt catalog ingestion workflow."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from datetime import date

from src.catalog_cursor import destination_key, next_destination_after, select_from_cursor
from src.catalog_dlt import candidate_records, ingest_cities, ingest_destinations, rotate_destinations
from src.destination_queue import DestinationQueueItem, load_destination_queue
from src.catalog_import import CatalogCandidate, CatalogImportError, CitySource, supported_city


class CatalogDltTests(unittest.TestCase):
    def test_candidate_records_keep_provenance_and_quality_outcome(self) -> None:
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
            retrieved_at="2026-08-03T00:00:00+00:00",
        )
        self.assertEqual(records[0]["quality_outcome"], "auto_publish")
        self.assertEqual(records[0]["city"], "Rome")

    @patch("src.catalog_dlt.resolve_city")
    @patch("src.catalog_dlt.dlt.pipeline")
    def test_ingestion_publishes_safe_attractions_and_appends_provenance(
        self,
        mock_pipeline_factory: MagicMock,
        mock_resolve_city: MagicMock,
    ) -> None:
        city = supported_city("Rome")
        mock_resolve_city.return_value = city
        pipeline = mock_pipeline_factory.return_value
        candidate = CatalogCandidate("Q123", "Example museum", 41.9, 12.5, "https://example.test", ("museum",), ())

        with tempfile.TemporaryDirectory() as directory:
            summary = ingest_cities(
                ["Rome"], "Italy", destination_path=Path(directory) / "ingestion.duckdb",
                catalog_path=Path(directory) / "activities.json", fetcher=lambda _: [candidate],
            )

        self.assertEqual(summary.published_by_city, {"Rome": 1})
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
        selection = rotate_destinations(destinations, 2, run_date=date(2026, 7, 20))
        self.assertEqual([item.city for item in selection], ["One", "Two"])
        next_week = rotate_destinations(destinations, 2, run_date=date(2026, 7, 27))
        self.assertEqual([item.city for item in next_week], ["Three", "Four"])
        all_destinations = rotate_destinations(destinations, 0, run_date=date(2026, 7, 20))
        self.assertEqual([item.city for item in all_destinations], ["One", "Two", "Three", "Four", "Five"])

    def test_persistent_cursor_continues_after_the_prior_batch(self) -> None:
        destinations = [
            DestinationQueueItem(city=city, country="Example")
            for city in ("One", "Two", "Three", "Four", "Five")
        ]
        selected = select_from_cursor(destinations, 2, destination_key(destinations[2]))
        self.assertEqual([item.city for item in selected], ["Three", "Four"])
        self.assertEqual(next_destination_after(destinations, selected[-1]), destination_key(destinations[4]))

    @patch("src.catalog_dlt.dlt.pipeline")
    @patch("src.catalog_dlt.resolve_city")
    def test_destination_queue_items_keep_their_own_countries(
        self,
        mock_resolve_city: MagicMock,
        mock_pipeline_factory: MagicMock,
    ) -> None:
        mock_resolve_city.side_effect = [supported_city("Rome"), supported_city("Florence")]
        with tempfile.TemporaryDirectory() as directory:
            ingest_destinations(
                [DestinationQueueItem(city="Rome", country="Italy"), DestinationQueueItem(city="Paris", country="France")],
                destination_path=Path(directory) / "ingestion.duckdb",
                catalog_path=Path(directory) / "activities.json", fetcher=lambda _: [],
            )
        self.assertEqual(mock_resolve_city.call_args_list[0].args, ("Rome", "Italy"))
        self.assertEqual(mock_resolve_city.call_args_list[1].args, ("Paris", "France"))

    @patch("src.catalog_dlt.dlt.pipeline")
    @patch("src.catalog_dlt.resolve_city")
    def test_ingestion_skips_one_temporary_source_failure_and_continues(
        self,
        mock_resolve_city: MagicMock,
        mock_pipeline_factory: MagicMock,
    ) -> None:
        rome = supported_city("Rome")
        florence = supported_city("Florence")
        mock_resolve_city.side_effect = [rome, florence]
        candidate = CatalogCandidate(
            "Q123", "Example museum", 41.9, 12.5, "https://example.test", ("museum",), ()
        )

        def fetcher(city: CitySource) -> list[CatalogCandidate]:
            if city.name == "Rome":
                raise CatalogImportError("Wikidata timed out")
            return [candidate]

        with tempfile.TemporaryDirectory() as directory:
            summary = ingest_destinations(
                [
                    DestinationQueueItem(city="Rome", country="Italy"),
                    DestinationQueueItem(city="Florence", country="Italy"),
                ],
                destination_path=Path(directory) / "ingestion.duckdb",
                catalog_path=Path(directory) / "activities.json",
                fetcher=fetcher,
            )

        self.assertEqual(summary.published_by_city, {"Florence": 1})
        self.assertEqual(len(summary.failures), 1)
        mock_pipeline_factory.return_value.run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
