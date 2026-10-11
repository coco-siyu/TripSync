"""Safely insert packaged catalog additions missing from live Supabase."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from src.catalog import (
    ACTIVITY_CATALOG_PATH,
    SUPABASE_CATALOG_TABLE,
    load_packaged_activities,
)
from src.models import Activity
from src.supabase_store import insert_many, is_configured, select


@dataclass(frozen=True)
class CatalogSyncPlan:
    """One destination's packaged records missing from the live catalog."""

    city: str
    country: str
    packaged_count: int
    remote_count: int
    missing: tuple[Activity, ...]


def build_sync_plan(
    city: str,
    country: str,
    *,
    catalog_path: Path = ACTIVITY_CATALOG_PATH,
) -> CatalogSyncPlan:
    """Compare packaged IDs with Supabase without changing either source."""

    if not is_configured():
        raise RuntimeError(
            "Configure SUPABASE_URL and SUPABASE_SECRET_KEY before syncing the catalog"
        )
    normalized_city = city.strip().casefold()
    normalized_country = country.strip().casefold()
    if not normalized_city or not normalized_country:
        raise ValueError("City and country are required")

    packaged = [
        activity
        for activity in load_packaged_activities(catalog_path)
        if activity.city.casefold() == normalized_city
        and activity.country.casefold() == normalized_country
    ]
    remote_rows = select(SUPABASE_CATALOG_TABLE, order="activity_id")
    remote_ids = {str(row.get("activity_id", "")) for row in remote_rows}
    remote_matching = [
        row
        for row in remote_rows
        if isinstance(row.get("activity_json"), dict)
        and str(row["activity_json"].get("city", "")).casefold() == normalized_city
        and str(row["activity_json"].get("country", "")).casefold()
        == normalized_country
    ]
    missing = tuple(activity for activity in packaged if activity.id not in remote_ids)
    return CatalogSyncPlan(
        city=city.strip(),
        country=country.strip(),
        packaged_count=len(packaged),
        remote_count=len(remote_matching),
        missing=missing,
    )


def apply_sync_plan(plan: CatalogSyncPlan, *, expected_missing: int) -> int:
    """Insert exactly the reviewed missing records, refusing count drift."""

    if expected_missing != len(plan.missing):
        raise ValueError(
            f"Expected {expected_missing} missing activities, but found {len(plan.missing)}. "
            "Run the dry-run command again and review the list before applying."
        )
    insert_many(
        SUPABASE_CATALOG_TABLE,
        [
            {
                "activity_id": activity.id,
                "activity_json": activity.model_dump(mode="json"),
            }
            for activity in plan.missing
        ],
    )
    return len(plan.missing)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Insert packaged activities that are missing from Supabase."
    )
    parser.add_argument("--city", default="Rome")
    parser.add_argument("--country", default="Italy")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Insert missing records. Without this flag the command is read-only.",
    )
    parser.add_argument(
        "--expect",
        type=int,
        help="Required with --apply; abort if the missing count differs.",
    )
    return parser


def main() -> None:
    load_dotenv()
    arguments = _parser().parse_args()
    if arguments.apply and arguments.expect is None:
        raise SystemExit("--apply requires --expect with the reviewed missing count")

    plan = build_sync_plan(arguments.city, arguments.country)
    print(f"Packaged {plan.city} activities: {plan.packaged_count}")
    print(f"Live Supabase {plan.city} activities: {plan.remote_count}")
    print(f"Missing from Supabase: {len(plan.missing)}")
    for activity in plan.missing:
        print(f"- {activity.id}: {activity.name}")

    if not arguments.apply:
        print("Dry run only. No database records were changed.")
        if plan.missing:
            print(
                "After reviewing the list, rerun with "
                f"--apply --expect {len(plan.missing)}"
            )
        return

    inserted = apply_sync_plan(plan, expected_missing=arguments.expect)
    print(f"Inserted {inserted} missing activities into Supabase.")


if __name__ == "__main__":
    main()
