"""Batch-import and auto-curate a city while retaining reviewable provenance."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.catalog import (
    CANDIDATE_DIRECTORY,
    auto_curate_candidates,
    save_activities,
)
from src.catalog_import import fetch_candidates, resolve_city, write_candidate_file


def main() -> None:
    """Fetch one city, create validated drafts, and optionally publish a batch."""

    parser = argparse.ArgumentParser(description="Batch-curate Wikidata candidates for TripSync.")
    parser.add_argument("--city", required=True, help="City name to retrieve.")
    parser.add_argument("--country", required=True, help="Country name for the city.")
    parser.add_argument("--limit", type=int, default=50, help="Candidates to retrieve (1–250).")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Explicitly add Pydantic-valid, auto-eligible drafts to activities.json.",
    )
    arguments = parser.parse_args()

    city = resolve_city(arguments.city, arguments.country)
    candidates = fetch_candidates(city, limit=arguments.limit)
    candidate_path = write_candidate_file(city, candidates, CANDIDATE_DIRECTORY)
    candidate_dicts = [candidate.__dict__ for candidate in candidates]
    activities, skipped = auto_curate_candidates(candidate_dicts, city.name, city.country)
    print(f"Saved {len(candidates)} source candidates to {candidate_path}")
    print(f"Prepared {len(activities)} Pydantic-valid activity drafts; skipped {len(skipped)}.")
    if not arguments.apply:
        print("Review in Curate catalog, or rerun with --apply to publish this batch.")
        return
    added, duplicates = save_activities(activities)
    print(f"Published {len(added)} activities; skipped {len(duplicates)} existing duplicates.")


if __name__ == "__main__":
    main()
