"""Scheduled quality-gated catalog ingestion using dlt and DuckDB.

The pipeline records Wikidata provenance, filters obvious non-activities, and
publishes only deterministic, Pydantic-valid visitor attractions. Ambiguous
records are stored in a separate review queue and never appear to travelers.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Callable, Iterable
from uuid import uuid4

import dlt

from src.catalog import auto_curate_candidates, save_activities, save_review_candidates
from src.catalog_import import (
    CatalogCandidate,
    CatalogImportError,
    CitySource,
    fetch_candidates,
    resolve_city,
)
from src.catalog_cursor import (
    advance_cursor,
    destination_key,
    next_destination_after,
    read_or_initialize_cursor,
    record_run,
    select_from_cursor,
)
from src.draft_curation import classify_candidate
from src.destination_queue import (
    DEFAULT_DESTINATION_QUEUE_PATH,
    DestinationQueueItem,
    load_destination_queue,
    load_initial_destination,
)
from src.official_sites import enrich_candidate_official_sites
from src.supabase_store import is_configured


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DESTINATION = REPOSITORY_ROOT / "data" / "tripsync_ingestion.duckdb"
# The first planned weekly catalog batch.  Rotation is relative to this date,
# rather than the calendar's absolute week number, so it always begins at the
# top of the destination queue and remains predictable across years.
ROTATION_START_DATE = date(2026, 7, 20)


@dataclass(frozen=True)
class IngestionOutcome:
    """Published counts and recoverable per-destination retrieval failures."""

    published_by_city: dict[str, int]
    failures: dict[str, str]


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
    week_index = max(0, (effective_date - ROTATION_START_DATE).days // 7)
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
) -> IngestionOutcome:
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
    failures: dict[str, str] = {}
    for destination in destination_list:
        try:
            city = resolve_city(destination.city, destination.country)
            candidates = (
                fetcher(city) if fetcher else fetch_candidates(city, limit=limit)
            )
        except (CatalogImportError, ValueError) as error:
            label = f"{destination.city}, {destination.country}"
            failures[destination_key(destination)] = str(error)
            print(f"Skipping {label}: {error}")
            continue
        retrieved_at = datetime.now(UTC).isoformat()
        records = candidate_records(city, candidates, retrieved_at=retrieved_at)
        if records:
            pipeline.run(records, table_name="candidate_runs", write_disposition="append")
        raw_candidates = [asdict(candidate) for candidate in candidates]
        enriched_candidates = enrich_candidate_official_sites(raw_candidates)
        activities, _ = auto_curate_candidates(
            enriched_candidates, city.name, city.country
        )
        save_review_candidates(enriched_candidates, city.name, city.country)
        added, _ = save_activities(activities, catalog_path) if catalog_path else save_activities(activities)
        summary[city.name] = len(added)
    return IngestionOutcome(summary, failures)


def ingest_cities(
    cities: Iterable[str],
    country: str,
    **kwargs: object,
) -> IngestionOutcome:
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
        outcome = ingest_cities(arguments.cities, arguments.country, limit=arguments.limit)
    else:
        queue = load_destination_queue(arguments.destination_queue)
        initial_destination = load_initial_destination(arguments.destination_queue) or queue[0]
        uses_cursor = arguments.rotate_size > 0 and is_configured()
        if uses_cursor:
            cursor = read_or_initialize_cursor(initial_destination)
            destinations = select_from_cursor(queue, arguments.rotate_size, cursor)
            print(
                "Catalog cursor: starting at "
                f"{destinations[0].city}; processing {len(destinations)} destination(s)."
            )
        else:
            destinations = rotate_destinations(queue, arguments.rotate_size)
        outcome = ingest_destinations(destinations, limit=arguments.limit)

        if uses_cursor:
            run_id = str(uuid4())
            for destination in destinations:
                key = destination_key(destination)
                if key in outcome.failures:
                    record_run(
                        destination,
                        status="failed",
                        error_message=outcome.failures[key],
                        run_id=run_id,
                    )
                else:
                    record_run(
                        destination,
                        status="succeeded",
                        published_count=outcome.published_by_city.get(destination.city, 0),
                        run_id=run_id,
                    )
            failed_destination = next(
                (item for item in destinations if destination_key(item) in outcome.failures),
                None,
            )
            next_key = (
                destination_key(failed_destination)
                if failed_destination
                else next_destination_after(queue, destinations[-1])
            )
            advance_cursor(next_key)
            next_label = next(item for item in queue if destination_key(item) == next_key)
            print(f"Catalog cursor: next run starts at {next_label.city}, {next_label.country}.")

    for city, count in outcome.published_by_city.items():
        print(f"{city}: published {count} new quality-approved attractions.")
    if outcome.failures:
        raise CatalogImportError(
            "One or more destinations need a retry: "
            + "; ".join(outcome.failures.values())
        )


if __name__ == "__main__":
    main()
