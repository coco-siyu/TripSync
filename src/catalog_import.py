"""Fetch reviewable attraction candidates from Wikidata.

This module deliberately does *not* create ``Activity`` records.  Wikidata is
useful for identifying places and their stable identifiers, but it cannot safely
provide TripSync's curated planning fields such as expected walking, duration,
budget, or accessibility.  Candidates must be reviewed before being added to
the application catalog.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"
WIKIDATA_ENTITY_URL = "https://www.wikidata.org/wiki/"
TRANSIENT_HTTP_STATUSES = {429, 500, 502, 503, 504}


class CatalogImportError(RuntimeError):
    """A friendly, actionable failure while contacting a catalog source."""


@dataclass(frozen=True)
class CitySource:
    """A supported city and the Wikidata entity used as its search centre."""

    name: str
    country: str
    wikidata_id: str


SUPPORTED_CITIES: dict[str, CitySource] = {
    "rome": CitySource("Rome", "Italy", "Q220"),
    "florence": CitySource("Florence", "Italy", "Q2044"),
    "milan": CitySource("Milan", "Italy", "Q490"),
    "venice": CitySource("Venice", "Italy", "Q641"),
}


@dataclass(frozen=True)
class CatalogCandidate:
    """A place discovered from Wikidata, ready for human review."""

    wikidata_id: str
    name: str
    latitude: float
    longitude: float
    source_url: str
    wikipedia_url: str | None = None
    image_url: str | None = None


def _city_key(value: str) -> str:
    return " ".join(value.casefold().split())


def supported_city(value: str) -> CitySource:
    """Return a configured city or explain which cities the importer supports."""

    key = _city_key(value)
    try:
        return SUPPORTED_CITIES[key]
    except KeyError as error:
        choices = ", ".join(city.name for city in SUPPORTED_CITIES.values())
        raise ValueError(f"Unsupported city {value!r}. Choose one of: {choices}.") from error


def build_query(city: CitySource, *, limit: int = 100) -> str:
    """Build the bounded, attraction-focused SPARQL query for one city."""

    if not 1 <= limit <= 250:
        raise ValueError("limit must be between 1 and 250")

    return f"""
SELECT DISTINCT ?item ?itemLabel ?coord ?article ?image ?sitelinks WHERE {{
  # Administrative containment avoids nearby towns and city-wide events that
  # a simple radius search can accidentally include.
  ?item wdt:P131* wd:{city.wikidata_id} ;
        wdt:P625 ?coord ;
        wikibase:sitelinks ?sitelinks .
  FILTER(?item != wd:{city.wikidata_id})
  OPTIONAL {{
    ?article schema:about ?item ;
             schema:isPartOf <https://en.wikipedia.org/> .
  }}
  OPTIONAL {{ ?item wdt:P18 ?image . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,it" . }}
}}
ORDER BY DESC(?sitelinks)
LIMIT {limit}
""".strip()


def _wikidata_id(entity_url: str) -> str:
    entity_id = entity_url.rsplit("/", 1)[-1]
    if not entity_id.startswith("Q") or not entity_id[1:].isdigit():
        raise ValueError(f"Unexpected Wikidata entity URL: {entity_url}")
    return entity_id


def _coordinates(value: str) -> tuple[float, float]:
    """Parse Wikidata's ``Point(longitude latitude)`` coordinate value."""

    try:
        longitude, latitude = value.removeprefix("Point(").removesuffix(")").split()
        return float(latitude), float(longitude)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Unexpected Wikidata coordinate: {value!r}") from error


def parse_candidates(payload: dict[str, Any]) -> list[CatalogCandidate]:
    """Convert a Wikidata SPARQL JSON response into deduplicated candidates."""

    candidates: list[CatalogCandidate] = []
    seen_ids: set[str] = set()
    bindings = payload.get("results", {}).get("bindings", [])
    if not isinstance(bindings, list):
        raise ValueError("Wikidata response does not contain a bindings list")

    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        try:
            entity_url = binding["item"]["value"]
            name = binding["itemLabel"]["value"].strip()
            latitude, longitude = _coordinates(binding["coord"]["value"])
            wikidata_id = _wikidata_id(entity_url)
        except (KeyError, AttributeError, ValueError):
            continue
        if not name or wikidata_id in seen_ids:
            continue
        seen_ids.add(wikidata_id)
        candidates.append(
            CatalogCandidate(
                wikidata_id=wikidata_id,
                name=name,
                latitude=latitude,
                longitude=longitude,
                source_url=f"{WIKIDATA_ENTITY_URL}{wikidata_id}",
                wikipedia_url=binding.get("article", {}).get("value"),
                image_url=binding.get("image", {}).get("value"),
            )
        )
    return sorted(candidates, key=lambda candidate: (candidate.name.casefold(), candidate.wikidata_id))


def fetch_candidates(
    city: CitySource,
    *,
    limit: int = 100,
    timeout: int = 30,
    retries: int = 3,
) -> list[CatalogCandidate]:
    """Request candidate places from Wikidata's public SPARQL endpoint."""

    if retries < 0:
        raise ValueError("retries must not be negative")

    query = build_query(city, limit=limit)
    request = Request(
        f"{WIKIDATA_SPARQL_URL}?{urlencode({'query': query, 'format': 'json'})}",
        headers={
            "Accept": "application/sparql-results+json",
            "User-Agent": "TripSync catalog importer (educational project)",
        },
    )
    for attempt in range(retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS endpoint
                payload = json.load(response)
            break
        except HTTPError as error:
            if error.code not in TRANSIENT_HTTP_STATUSES or attempt == retries:
                raise CatalogImportError(
                    f"Wikidata returned HTTP {error.code} for {city.name}. "
                    "Please try again later."
                ) from error
        except URLError as error:
            if attempt == retries:
                raise CatalogImportError(
                    f"Could not reach Wikidata for {city.name}. Check your "
                    "connection and try again."
                ) from error

        # Public query services occasionally have short outages. Exponential
        # backoff is polite to the service and usually resolves these quickly.
        time.sleep(2**attempt)

    if not isinstance(payload, dict):
        raise ValueError("Wikidata response must be a JSON object")
    return parse_candidates(payload)


def write_candidate_file(
    city: CitySource,
    candidates: list[CatalogCandidate],
    output_directory: Path,
) -> Path:
    """Write a reproducible, review-only JSON file for one city."""

    output_directory.mkdir(parents=True, exist_ok=True)
    output_path = output_directory / f"{_city_key(city.name)}_wikidata_candidates.json"
    document = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "city": city.name,
        "country": city.country,
        "source": {
            "name": "Wikidata Query Service",
            "query_url": WIKIDATA_SPARQL_URL,
            "city_wikidata_id": city.wikidata_id,
            "license": "CC0 for Wikidata structured data",
            "attribution": "Data sourced from Wikidata.",
        },
        "candidates": [asdict(candidate) for candidate in candidates],
        "review_note": (
            "These are discovery candidates only. Do not copy an item into "
            "activities.json until its TripSync fields have been manually reviewed."
        ),
    }
    output_path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output_path


def main() -> None:
    """Run the candidate importer as a small command-line tool."""

    parser = argparse.ArgumentParser(description="Fetch review-only Wikidata attraction candidates.")
    parser.add_argument(
        "--cities",
        nargs="+",
        default=[city.name for city in SUPPORTED_CITIES.values()],
        help="One or more supported cities (default: Rome Florence Milan Venice).",
    )
    parser.add_argument("--limit", type=int, default=100, help="Maximum candidates per city (1-250).")
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Retries for temporary Wikidata errors (default: 3).",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("data/candidates"))
    arguments = parser.parse_args()

    for city_name in arguments.cities:
        city = supported_city(city_name)
        try:
            candidates = fetch_candidates(
                city,
                limit=arguments.limit,
                retries=arguments.retries,
            )
        except (CatalogImportError, ValueError) as error:
            parser.exit(1, f"Could not import {city.name}: {error}\n")
        output_path = write_candidate_file(city, candidates, arguments.output_dir)
        print(f"{city.name}: wrote {len(candidates)} candidates to {output_path}")


if __name__ == "__main__":
    main()
