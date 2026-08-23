"""Deterministic coverage reporting for official visitor information."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from src.models import Activity


@dataclass(frozen=True)
class CatalogQualitySummary:
    total: int
    verified_official_sites: int
    official_hours_links: int
    official_ticket_links: int
    addresses: int
    coordinates: int


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
