"""Scheduled quality-gated catalog ingestion using dlt and DuckDB.

The pipeline records Wikidata provenance, filters obvious non-activities, and
publishes only deterministic, Pydantic-valid visitor attractions. Ambiguous
records are counted for audit but do not become a routine curation task.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Callable, Iterable

import dlt

from src.catalog import auto_curate_candidates, save_activities
from src.catalog_import import CatalogCandidate, CitySource, fetch_candidates, resolve_city
from src.draft_curation import classify_candidate
from src.destination_queue import DEFAULT_DESTINATION_QUEUE_PATH, DestinationQueueItem, load_destination_queue


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DESTINATION = REPOSITORY_ROOT / "data" / "tripsync_ingestion.duckdb"


def rotate_destinations(
    destinations: Iterable[DestinationQueueItem],
    batch_size: int,
    *,
    run_date: date | None = None,
) -> list[DestinationQueueItem]:
    """Choose a stable weekly slice so a large queue is refreshed fairly."""

    destination_list = list(destinations)
    if not destination_list:
        raise ValueError("at least one destination is required")
    if batch_size < 1:
        return destination_list
    if batch_size >= len(destination_list):
        return destination_list
    effective_date = run_date or datetime.now(UTC).date()
    week_index = effective_date.toordinal() // 7
    start = (week_index * batch_size) % len(destination_list)
    return [
        destination_list[(start + offset) % len(destination_list)]
        for offset in range(batch_size)
    ]


def candidate_records(
    city: CitySource,
    candidates: Iterable[CatalogCandidate],
    *,
    retrieved_at: str,
) -> list[dict[str, object]]:
    """Return provenance records for dlt without retaining local JSON batches."""

    records: list[dict[str, object]] = []
    for candidate in candidates:
        record = asdict(candidate)
        record.update(
            {
                "record_id": f"{city.wikidata_id}:{candidate.wikidata_id}:{retrieved_at}",
                "city": city.name,
                "country": city.country,
                "city_wikidata_id": city.wikidata_id,
                "retrieved_at": retrieved_at,
                "quality_outcome": classify_candidate(record).outcome,
            }
        )
        records.append(record)
    return records


def ingest_destinations(
    destinations: Iterable[DestinationQueueItem],
    *,
    limit: int = 50,
    destination_path: Path = DEFAULT_DESTINATION,
    catalog_path: Path | None = None,
    fetcher: Callable[[CitySource], list[CatalogCandidate]] | None = None,
) -> dict[str, int]:
    """Fetch configured destinations and publish safe attractions directly."""

    if not 1 <= limit <= 250:
        raise ValueError("limit must be between 1 and 250")
    destination_list = list(destinations)
    if not destination_list:
        raise ValueError("at least one destination is required")

    pipeline = dlt.pipeline(
        pipeline_name="tripsync_catalog_ingestion",
        destination=dlt.destinations.duckdb(str(destination_path)),
        dataset_name="catalog_ingestion",
        pipelines_dir=str(REPOSITORY_ROOT / ".dlt" / "pipelines"),
    )
    summary: dict[str, int] = {}
    for destination in destination_list:
        city = resolve_city(destination.city, destination.country)
        candidates = fetcher(city) if fetcher else fetch_candidates(city, limit=limit)
        retrieved_at = datetime.now(UTC).isoformat()
        records = candidate_records(city, candidates, retrieved_at=retrieved_at)
        if records:
            pipeline.run(records, table_name="candidate_runs", write_disposition="append")
        activities, _ = auto_curate_candidates(
            [asdict(candidate) for candidate in candidates], city.name, city.country
        )
        added, _ = save_activities(activities, catalog_path) if catalog_path else save_activities(activities)
        summary[city.name] = len(added)
    return summary


def ingest_cities(
    cities: Iterable[str],
    country: str,
    **kwargs: object,
) -> dict[str, int]:
    """Convenience wrapper for an intentional, one-off city list."""

    destinations = [
        DestinationQueueItem(city=city, country=country)
        for city in cities
        if city.strip()
    ]
    return ingest_destinations(destinations, **kwargs)


def main() -> None:
    """Run the scheduled ingestion manually or from GitHub Actions."""

    parser = argparse.ArgumentParser(description="Retrieve quality-screened attractions with dlt.")
    parser.add_argument("--cities", nargs="+", help="One-off city list; requires --country.")
    parser.add_argument("--country", help="Shared country for --cities.")
    parser.add_argument(
        "--destination-queue", type=Path, default=DEFAULT_DESTINATION_QUEUE_PATH,
        help="Versioned destination queue used when --cities is omitted.",
    )
    parser.add_argument(
        "--rotate-size", type=int, default=0,
        help="For scheduled runs, retrieve this many queue destinations this week; 0 retrieves all.",
    )
    parser.add_argument("--limit", type=int, default=50, help="Candidates per city (1–250).")
    arguments = parser.parse_args()
    if arguments.cities:
        if not arguments.country:
            parser.error("--country is required with --cities")
        summary = ingest_cities(arguments.cities, arguments.country, limit=arguments.limit)
    else:
        destinations = rotate_destinations(
            load_destination_queue(arguments.destination_queue), arguments.rotate_size
        )
        summary = ingest_destinations(
            destinations, limit=arguments.limit
        )
    for city, count in summary.items():
        print(f"{city}: published {count} new quality-approved attractions.")


if __name__ == "__main__":
    main()
