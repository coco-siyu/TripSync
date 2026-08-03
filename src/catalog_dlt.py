"""Scheduled, review-only catalog ingestion using dlt and DuckDB.

The pipeline records what was retrieved from Wikidata and writes the same raw
candidate batches that the Curate catalog workspace expects.  It deliberately
does not call ``save_activities``: publishing remains a human approval step.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Iterable

import dlt

from src.catalog import CANDIDATE_DIRECTORY
from src.catalog_import import CatalogCandidate, CitySource, fetch_candidates, resolve_city, write_candidate_file
from src.destination_queue import DEFAULT_DESTINATION_QUEUE_PATH, DestinationQueueItem, load_destination_queue


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DESTINATION = REPOSITORY_ROOT / "data" / "tripsync_ingestion.duckdb"


def _candidate_batch_label(path: Path) -> str:
    """Use a portable label for the human-review batch location."""

    try:
        return str(path.relative_to(REPOSITORY_ROOT))
    except ValueError:
        return str(path)


def candidate_records(
    city: CitySource,
    candidates: Iterable[CatalogCandidate],
    candidate_path: Path,
    *,
    retrieved_at: str,
) -> list[dict[str, object]]:
    """Return provenance records for dlt without treating candidates as published."""

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
                "candidate_batch": _candidate_batch_label(candidate_path),
                "status": "pending_human_review",
            }
        )
        records.append(record)
    return records


def ingest_destinations(
    destinations: Iterable[DestinationQueueItem],
    *,
    limit: int = 50,
    candidate_directory: Path = CANDIDATE_DIRECTORY,
    destination_path: Path = DEFAULT_DESTINATION,
    fetcher: Callable[[CitySource], list[CatalogCandidate]] | None = None,
) -> dict[str, int]:
    """Fetch configured destinations, save review files, and append provenance via dlt."""

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
        candidate_path = write_candidate_file(city, candidates, candidate_directory)
        retrieved_at = datetime.now(UTC).isoformat()
        records = candidate_records(city, candidates, candidate_path, retrieved_at=retrieved_at)
        if records:
            pipeline.run(records, table_name="candidate_runs", write_disposition="append")
        summary[city.name] = len(candidates)
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

    parser = argparse.ArgumentParser(description="Retrieve review-only attraction batches with dlt.")
    parser.add_argument("--cities", nargs="+", help="One-off city list; requires --country.")
    parser.add_argument("--country", help="Shared country for --cities.")
    parser.add_argument(
        "--destination-queue", type=Path, default=DEFAULT_DESTINATION_QUEUE_PATH,
        help="Versioned JSON queue used when --cities is omitted.",
    )
    parser.add_argument("--limit", type=int, default=50, help="Candidates per city (1–250).")
    arguments = parser.parse_args()
    if arguments.cities:
        if not arguments.country:
            parser.error("--country is required with --cities")
        summary = ingest_cities(arguments.cities, arguments.country, limit=arguments.limit)
    else:
        summary = ingest_destinations(
            load_destination_queue(arguments.destination_queue), limit=arguments.limit
        )
    for city, count in summary.items():
        print(f"{city}: saved {count} review candidates; none were published.")


if __name__ == "__main__":
    main()
