"""Optional OpenStreetMap detail enrichment for already-curated activities.

This module is intentionally used by a backfill command, not while rendering
the Streamlit UI. OSM is a useful supplementary source for addresses and
opening-hours tags, but the attraction's official site remains the live source
for tickets, access, and time-sensitive visitor information.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from collections.abc import Iterable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from src.catalog_import import CatalogImportError
from src.models import Activity


# Overpass is a shared public service. Keep a small, explicit fallback list so
# a temporary issue on one instance does not make a manual backfill unusable.
# This work never runs while the Streamlit app is rendering.
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
WIKIDATA_ENTITY_URL = "https://www.wikidata.org/wiki/"


@dataclass(frozen=True)
class OsmDetails:
    """Supplementary, community-maintained visit details from OpenStreetMap."""

    address: str | None = None
    opening_hours: str | None = None
    website: str | None = None
    osm_url: str | None = None


def _address(tags: dict[str, Any]) -> str | None:
    """Format the useful address tags while tolerating partial OSM coverage."""

    full = str(tags.get("addr:full", "")).strip()
    if full:
        return full
    street = " ".join(
        part for part in (str(tags.get("addr:housenumber", "")).strip(), str(tags.get("addr:street", "")).strip()) if part
    )
    locality = " ".join(
        part for part in (str(tags.get("addr:postcode", "")).strip(), str(tags.get("addr:city", "")).strip()) if part
    )
    parts = [part for part in (street, locality, str(tags.get("addr:country", "")).strip()) if part]
    return ", ".join(parts) or None


def parse_osm_details(payload: dict[str, Any]) -> OsmDetails:
    """Extract the first usable element from a constrained Overpass response."""

    elements = payload.get("elements", [])
    if not isinstance(elements, list) or not elements:
        return OsmDetails()
    element = next((entry for entry in elements if isinstance(entry, dict)), None)
    if element is None:
        return OsmDetails()
    tags = element.get("tags", {})
    if not isinstance(tags, dict):
        tags = {}
    element_type = str(element.get("type", "")).strip()
    element_id = element.get("id")
    osm_url = (
        f"https://www.openstreetmap.org/{element_type}/{element_id}"
        if element_type in {"node", "way", "relation"} and isinstance(element_id, int)
        else None
    )
    website = str(tags.get("website") or tags.get("contact:website") or "").strip() or None
    return OsmDetails(
        address=_address(tags),
        opening_hours=str(tags.get("opening_hours", "")).strip() or None,
        website=website,
        osm_url=osm_url,
    )


def parse_osm_details_by_wikidata(payload: dict[str, Any]) -> dict[str, OsmDetails]:
    """Index usable OSM elements by their stable Wikidata tag."""

    elements = payload.get("elements", [])
    if not isinstance(elements, list):
        return {}

    details_by_wikidata: dict[str, OsmDetails] = {}
    for element in elements:
        if not isinstance(element, dict):
            continue
        tags = element.get("tags", {})
        if not isinstance(tags, dict):
            continue
        wikidata_id = str(tags.get("wikidata", "")).strip()
        if not (wikidata_id.startswith("Q") and wikidata_id[1:].isdigit()):
            continue
        details_by_wikidata.setdefault(wikidata_id, parse_osm_details({"elements": [element]}))
    return details_by_wikidata


def _wikidata_id(activity: Activity) -> str | None:
    """Use the dedicated ID when present, or recover it from older records."""

    if activity.wikidata_id:
        return activity.wikidata_id
    source_url = str(activity.source_url)
    if not source_url.startswith(WIKIDATA_ENTITY_URL):
        return None
    identifier = source_url.removeprefix(WIKIDATA_ENTITY_URL).split("?", 1)[0].strip()
    return identifier if identifier.startswith("Q") and identifier[1:].isdigit() else None


def _query(activity: Activity) -> str:
    """Prefer the stable Wikidata item; name/location is a safe fallback."""

    wikidata_id = _wikidata_id(activity)
    if wikidata_id:
        selector = f'["wikidata"="{wikidata_id}"]'
    elif activity.latitude is not None and activity.longitude is not None:
        name = activity.name.replace('"', '\\"')
        selector = f'["name"="{name}"](around:350,{activity.latitude},{activity.longitude})'
    else:
        return ""
    return f"[out:json][timeout:20];nwr{selector};out tags center 2;"


def _batch_query(wikidata_ids: Iterable[str]) -> str:
    """Build one exact-ID Overpass query for a small batch of activities."""

    ids = [item_id for item_id in wikidata_ids if item_id.startswith("Q") and item_id[1:].isdigit()]
    if not ids:
        return ""
    return f'[out:json][timeout:25];nwr["wikidata"~"^({"|".join(ids)})$"];out tags center;'


def _request_overpass(query: str, *, timeout: int) -> dict[str, Any]:
    """Fetch one constrained Overpass query with a public endpoint fallback."""

    errors: list[Exception] = []
    for endpoint in OVERPASS_URLS:
        request = Request(
            f"{endpoint}?{urlencode({'data': query})}",
            headers={"Accept": "application/json", "User-Agent": "TripSync catalog enrichment (educational project)"},
        )
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS endpoints
                payload = json.load(response)
        except (HTTPError, URLError, TimeoutError) as error:
            errors.append(error)
            continue
        return payload if isinstance(payload, dict) else {}

    raise CatalogImportError(
        "OpenStreetMap details are temporarily unavailable after trying two public endpoints. Try again later."
    ) from errors[-1]


def fetch_osm_details(activity: Activity, *, timeout: int = 15) -> OsmDetails:
    """Look up one activity on OSM without changing it or making UI calls."""

    query = _query(activity)
    if not query:
        return OsmDetails()
    return parse_osm_details(_request_overpass(query, timeout=timeout))


def fetch_osm_details_many(
    activities: Iterable[Activity], *, timeout: int = 20, batch_size: int = 12
) -> dict[str, OsmDetails]:
    """Fetch OSM details in modest stable-ID batches, not one request per row.

    Overpass is shared infrastructure. A failed batch is skipped so a single
    slow group cannot discard useful detail records found by earlier batches.
    """

    ids_by_activity = {
        activity.id: wikidata_id
        for activity in activities
        if (wikidata_id := _wikidata_id(activity))
    }
    details_by_wikidata: dict[str, OsmDetails] = {}
    unique_ids = sorted(set(ids_by_activity.values()))
    successful_batches = 0
    consecutive_failed_batches = 0
    for start in range(0, len(unique_ids), batch_size):
        query = _batch_query(unique_ids[start : start + batch_size])
        if query:
            try:
                payload = _request_overpass(query, timeout=timeout)
            except CatalogImportError:
                consecutive_failed_batches += 1
                # Stop quickly during a service outage instead of multiplying
                # one timeout across every remaining batch.
                if consecutive_failed_batches >= 2:
                    break
                continue
            consecutive_failed_batches = 0
            successful_batches += 1
            details_by_wikidata.update(parse_osm_details_by_wikidata(payload))
    if unique_ids and not successful_batches:
        raise CatalogImportError(
            "OpenStreetMap details are temporarily unavailable after trying two public endpoints. Try again later."
        )
    return {
        activity_id: details_by_wikidata[wikidata_id]
        for activity_id, wikidata_id in ids_by_activity.items()
        if wikidata_id in details_by_wikidata
    }


def enrich_activity(activity: Activity, details: OsmDetails) -> Activity | None:
    """Return an updated copy only when OSM contributes missing information."""

    updates: dict[str, str] = {}
    if details.address and not activity.address:
        updates["address"] = details.address
    if details.opening_hours and not activity.opening_hours:
        updates["opening_hours"] = details.opening_hours
    if details.osm_url and not activity.osm_url:
        updates["osm_url"] = details.osm_url
    if details.website and not activity.official_url:
        updates["official_url"] = details.website
    # ``model_copy(update=...)`` deliberately skips Pydantic validation.  That
    # left URL strings in fields declared as ``HttpUrl`` and produced serializer
    # warnings when the refreshed catalog was written.  Re-validating the full
    # record keeps enriched URLs typed exactly like curator-supplied URLs.
    return Activity.model_validate({**activity.model_dump(mode="json"), **updates}) if updates else None
