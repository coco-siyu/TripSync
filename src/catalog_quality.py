"""Deterministic release and visitor-information checks for the catalog."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re
from urllib.parse import urlparse

from src.models import Activity


ROME_CATALOG_TARGET = 40
MIN_CATEGORY_VARIETY = 6
MIN_LOCATION_ZONES = 4


@dataclass(frozen=True)
class CatalogQualitySummary:
    total: int
    verified_official_sites: int
    official_hours_links: int
    official_ticket_links: int
    addresses: int
    coordinates: int


@dataclass(frozen=True)
class CatalogReleaseSummary:
    """A compact, deterministic destination-level release gate."""

    total: int
    target: int
    release_ready: int
    categories: int
    budget_levels: int
    walking_levels: int
    location_zones: int

    @property
    def meets_target(self) -> bool:
        return self.total >= self.target

    @property
    def has_diversity(self) -> bool:
        return (
            self.categories >= MIN_CATEGORY_VARIETY
            and self.budget_levels >= 3
            and self.walking_levels >= 3
            and self.location_zones >= MIN_LOCATION_ZONES
        )

    @property
    def ready(self) -> bool:
        return (
            self.meets_target
            and self.release_ready == self.total
            and self.has_diversity
        )


def _source_hostname(activity: Activity) -> str:
    return (urlparse(str(activity.source_url)).hostname or "").casefold()


def activity_release_issues(activity: Activity) -> tuple[str, ...]:
    """Return blocking identity gaps not covered by the Activity model.

    Pydantic already requires every planning field. This additional gate makes
    sure a published record also has a stable source identity. A verified
    official site, a Wikidata identifier, or a place-specific first-party
    source all qualify; a bare Wikipedia or Wikidata URL without its stable ID
    does not.
    """

    hostname = _source_hostname(activity)
    is_wikidata_host = hostname == "wikidata.org" or hostname.endswith(
        ".wikidata.org"
    )
    is_locator_only_host = is_wikidata_host or hostname == "wikipedia.org" or hostname.endswith(
        ".wikipedia.org"
    )
    source_path = urlparse(str(activity.source_url)).path.rstrip("/")
    has_wikidata_entity_url = bool(
        is_wikidata_host
        and re.fullmatch(r"/(?:wiki/)?Q\d+", source_path)
    )
    has_source_identity = bool(
        activity.official_site_verified
        or activity.wikidata_id
        or has_wikidata_entity_url
        or (hostname and not is_locator_only_host)
    )
    return () if has_source_identity else ("Reliable source identity missing",)


def _location_zone(activity: Activity) -> tuple[int, int] | None:
    """Group nearby coordinates into stable city-area cells.

    The source catalog does not invent neighborhood names. Small coordinate
    cells give curators a deterministic proxy for checking that one central
    cluster is not mistaken for destination-wide neighborhood coverage.
    """

    if activity.latitude is None or activity.longitude is None:
        return None
    return (round(activity.latitude / 0.015), round(activity.longitude / 0.02))


def summarize_destination_release(
    activities: list[Activity], *, target: int = ROME_CATALOG_TARGET
) -> CatalogReleaseSummary:
    """Summarize target progress, identity readiness, and planning diversity."""

    return CatalogReleaseSummary(
        total=len(activities),
        target=target,
        release_ready=sum(not activity_release_issues(item) for item in activities),
        categories=len({item.category for item in activities}),
        budget_levels=len({item.budget_level for item in activities}),
        walking_levels=len({item.walking_level for item in activities}),
        location_zones=len(
            {zone for item in activities if (zone := _location_zone(item)) is not None}
        ),
    )


def summarize_catalog_quality(
    activities: list[Activity],
) -> CatalogQualitySummary:
    """Count visitor-detail coverage without making network requests."""

    return CatalogQualitySummary(
        total=len(activities),
        verified_official_sites=sum(
            activity.official_site_verified for activity in activities
        ),
        official_hours_links=sum(
            activity.official_hours_url is not None for activity in activities
        ),
        official_ticket_links=sum(
            activity.official_tickets_url is not None for activity in activities
        ),
        addresses=sum(activity.address is not None for activity in activities),
        coordinates=sum(
            activity.latitude is not None and activity.longitude is not None
            for activity in activities
        ),
    )


def official_source_status(activity: Activity) -> str:
    if activity.official_site_verified:
        return "Verified official site"
    if activity.official_url:
        return "Awaiting automatic verification"
    return "Official site missing"


def catalog_quality_rows(activities: list[Activity]) -> list[dict[str, object]]:
    """Return safe, human-readable activity rows for the admin workspace."""

    return [
        {
            "Name": activity.name,
            "City": activity.city,
            "Country": activity.country,
            "Official source": official_source_status(activity),
            "Official website": str(activity.official_url or ""),
            "Hours page": str(activity.official_hours_url or ""),
            "Tickets page": str(activity.official_tickets_url or ""),
            "Address": bool(activity.address),
            "Coordinates": bool(
                activity.latitude is not None and activity.longitude is not None
            ),
            "Release ready": not activity_release_issues(activity),
            "Release issues": "; ".join(activity_release_issues(activity)),
        }
        for activity in activities
    ]


def destination_quality_rows(
    activities: list[Activity],
) -> list[dict[str, object]]:
    """Aggregate coverage by destination for prioritization."""

    grouped: dict[tuple[str, str], list[Activity]] = defaultdict(list)
    for activity in activities:
        grouped[(activity.city, activity.country)].append(activity)

    rows: list[dict[str, object]] = []
    for (city, country), destination_activities in sorted(
        grouped.items(),
        key=lambda item: (item[0][1].casefold(), item[0][0].casefold()),
    ):
        summary = summarize_catalog_quality(destination_activities)
        total = summary.total or 1
        rows.append(
            {
                "Destination": f"{city}, {country}",
                "Activities": summary.total,
                "Official sites": summary.verified_official_sites / total,
                "Hours links": summary.official_hours_links / total,
                "Ticket links": summary.official_ticket_links / total,
                "Addresses": summary.addresses / total,
                "Coordinates": summary.coordinates / total,
            }
        )
    return rows
