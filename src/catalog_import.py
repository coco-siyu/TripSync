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

from src.draft_curation import classify_candidate


WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"
WIKIDATA_ENTITY_URL = "https://www.wikidata.org/wiki/"
WIKIDATA_SEARCH_URL = "https://www.wikidata.org/w/api.php"
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
    wikidata_types: tuple[str, ...]
    review_flags: tuple[str, ...]
    wikipedia_url: str | None = None
    official_url: str | None = None
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


def resolve_city(city_name: str, country: str, *, timeout: int = 20) -> CitySource:
    """Resolve a free-text city and country to a Wikidata entity for import."""

    name = city_name.strip()
    country_name = country.strip()
    if not name or not country_name:
        raise ValueError("city and country are required")
    known = SUPPORTED_CITIES.get(_city_key(name))
    if known and known.country.casefold() == country_name.casefold():
        return known
    request = Request(
        f"{WIKIDATA_SEARCH_URL}?{urlencode({'action': 'wbsearchentities', 'search': name, 'language': 'en', 'format': 'json', 'type': 'item', 'limit': 20})}",
        headers={"Accept": "application/json", "User-Agent": "TripSync catalog importer (educational project)"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS endpoint
            payload = json.load(response)
    except (HTTPError, URLError) as error:
        raise CatalogImportError(f"Could not resolve {name}, {country_name}. Try again later.") from error
    results = payload.get("search", []) if isinstance(payload, dict) else []
    country_match = next((
        item for item in results
        if country_name.casefold() in str(item.get("description", "")).casefold()
        and any(word in str(item.get("description", "")).casefold() for word in ("city", "town", "municipality", "commune"))
    ), None)
    match = country_match or (results[0] if results else None)
    entity_id = match.get("id") if isinstance(match, dict) else None
    if not isinstance(entity_id, str) or not entity_id.startswith("Q"):
        raise CatalogImportError(f"No Wikidata city match found for {name}, {country_name}.")
    return CitySource(name=name, country=country_name, wikidata_id=entity_id)


def build_query(city: CitySource, *, limit: int = 100) -> str:
    """Build the bounded, attraction-focused SPARQL query for one city."""

    if not 1 <= limit <= 250:
        raise ValueError("limit must be between 1 and 250")

    return f"""
SELECT DISTINCT ?item ?itemLabel ?coord ?article ?officialWebsite ?image ?sitelinks ?type ?typeLabel WHERE {{
  # Limit unique places before optional type labels expand one place into
  # several result rows. Without this subquery, --limit 25 could mean five
  # attractions with five types each instead of 25 attractions.
  {{
    SELECT ?item ?coord ?sitelinks WHERE {{
      # Administrative containment avoids nearby towns and city-wide events
      # that a simple radius search can accidentally include.
      ?item wdt:P131* wd:{city.wikidata_id} ;
            wdt:P625 ?coord ;
            wikibase:sitelinks ?sitelinks .
      FILTER(?item != wd:{city.wikidata_id})
    }}
    ORDER BY DESC(?sitelinks)
    LIMIT {limit}
  }}
  OPTIONAL {{ ?item wdt:P31 ?type . }}
  OPTIONAL {{
    ?article schema:about ?item ;
             schema:isPartOf <https://en.wikipedia.org/> .
  }}
  OPTIONAL {{ ?item wdt:P856 ?officialWebsite . }}
  OPTIONAL {{ ?item wdt:P18 ?image . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,it" . }}
}}
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


def coordinates_query(wikidata_ids: list[str]) -> str:
    """Build a small coordinate-only query for known Wikidata items.

    This is deliberately separate from the discovery query: a catalog record
    can be refreshed by its stable source identifier without rediscovering or
    changing any of its curated planning details.
    """

    ids = sorted({item.strip() for item in wikidata_ids if item.strip()})
    if not ids:
        raise ValueError("at least one Wikidata identifier is required")
    if len(ids) > 100:
        raise ValueError("a coordinate query supports at most 100 identifiers")
    if any(not item.startswith("Q") or not item[1:].isdigit() for item in ids):
        raise ValueError("Wikidata identifiers must look like Q123")

    values = " ".join(f"wd:{item}" for item in ids)
    return f"""
SELECT ?item ?coord WHERE {{
  VALUES ?item {{ {values} }}
  ?item wdt:P625 ?coord .
}}
""".strip()


def fetch_coordinates(
    wikidata_ids: list[str], *, timeout: int = 20, retries: int = 3
) -> dict[str, tuple[float, float]]:
    """Look up coordinates for stable Wikidata IDs, with polite retries."""

    if retries < 0:
        raise ValueError("retries must not be negative")
    query = coordinates_query(wikidata_ids)
    request = Request(
        f"{WIKIDATA_SPARQL_URL}?{urlencode({'query': query, 'format': 'json'})}",
        headers={
            "Accept": "application/sparql-results+json",
            "User-Agent": "TripSync catalog coordinate backfill (educational project)",
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
                    f"Wikidata returned HTTP {error.code} while refreshing coordinates. "
                    "Please try again later."
                ) from error
        except (TimeoutError, URLError) as error:
            if attempt == retries:
                raise CatalogImportError(
                    "Wikidata timed out while refreshing coordinates. Try again later."
                ) from error
        time.sleep(2**attempt)

    bindings = payload.get("results", {}).get("bindings", []) if isinstance(payload, dict) else []
    coordinates: dict[str, tuple[float, float]] = {}
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        try:
            item_id = _wikidata_id(binding["item"]["value"])
            coordinates[item_id] = _coordinates(binding["coord"]["value"])
        except (KeyError, TypeError, ValueError):
            continue
    return coordinates


def _review_flags(types: set[str]) -> tuple[str, ...]:
    """Describe items that need context rather than silently excluding them."""

    normalized_types = " ".join(type_name.casefold() for type_name in types)
    flags: list[str] = []
    if "stadium" in normalized_types:
        flags.append("Event venue: keep only for a specific tour or event.")
    if "university" in normalized_types or "college" in normalized_types:
        flags.append("Institution: verify visitor access or a campus-tour experience.")
    if "library" in normalized_types:
        flags.append("Institution: verify visitor access and visitor relevance.")
    if "sculpture" in normalized_types or "artwork" in normalized_types:
        flags.append("Standalone artwork: prefer the venue where it can be visited.")
    return tuple(flags)


def _is_transport_infrastructure(types: set[str]) -> bool:
    """Filter travel infrastructure, which is outside the activity catalog."""

    normalized_types = " ".join(type_name.casefold() for type_name in types)
    return any(term in normalized_types for term in ("airport", "aerodrome", "airfield"))


def parse_candidates(payload: dict[str, Any]) -> list[CatalogCandidate]:
    """Convert a Wikidata SPARQL JSON response into deduplicated candidates."""

    candidates_by_id: dict[str, dict[str, Any]] = {}
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
        if not name:
            continue
        candidate = candidates_by_id.setdefault(
            wikidata_id,
            {
                "wikidata_id": wikidata_id,
                "name": name,
                "latitude": latitude,
                "longitude": longitude,
                "source_url": f"{WIKIDATA_ENTITY_URL}{wikidata_id}",
                "wikipedia_url": binding.get("article", {}).get("value"),
                "official_url": binding.get("officialWebsite", {}).get("value"),
                "image_url": binding.get("image", {}).get("value"),
                "types": set(),
            },
        )
        type_name = binding.get("typeLabel", {}).get("value")
        if isinstance(type_name, str) and type_name.strip():
            candidate["types"].add(type_name.strip())

    candidates = []
    for candidate in candidates_by_id.values():
        types = candidate.pop("types")
        candidate_record = CatalogCandidate(
                **candidate,
                wikidata_types=tuple(sorted(types, key=str.casefold)),
                review_flags=_review_flags(types),
            )
        # Keep raw review batches focused. We retain only records a traveller
        # could plausibly visit or records whose visitor relevance is unclear.
        # Clear non-activities are rejected before reaching the admin UI.
        if classify_candidate(candidate_record.__dict__).outcome != "reject":
            candidates.append(candidate_record)
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

    # Query a little wider than the requested result count so records rejected
    # by the quality gate do not leave the curator with a sparse batch.
    source_limit = min(limit * 3, 250)
    query = build_query(city, limit=source_limit)
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
        except (TimeoutError, URLError) as error:
            if attempt == retries:
                raise CatalogImportError(
                    f"Wikidata timed out or could not be reached for {city.name}. "
                    "Try again later."
                ) from error

        # Public query services occasionally have short outages. Exponential
        # backoff is polite to the service and usually resolves these quickly.
        time.sleep(2**attempt)

    if not isinstance(payload, dict):
        raise ValueError("Wikidata response must be a JSON object")
    return parse_candidates(payload)[:limit]


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
    parser.add_argument("--city", help="Any city to resolve through Wikidata, for example Naples.")
    parser.add_argument("--country", help="Country used to disambiguate --city, for example Italy.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum candidates per city (1-250).")
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Retries for temporary Wikidata errors (default: 3).",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("data/candidates"))
    arguments = parser.parse_args()

    if arguments.city and not arguments.country:
        parser.error("--country is required when using --city")
    cities = [resolve_city(arguments.city, arguments.country)] if arguments.city else [supported_city(city_name) for city_name in arguments.cities]
    for city in cities:
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
