"""Tests for the explicit packaged-to-Supabase catalog reconciliation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.catalog_sync import apply_sync_plan, build_sync_plan


class CatalogSyncTests(unittest.TestCase):
    def test_plan_lists_only_missing_destination_records(self) -> None:
        source = Path("data/sample_activities.json")
        records = json.loads(source.read_text(encoding="utf-8"))[:2]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "activities.json"
            path.write_text(json.dumps(records), encoding="utf-8")
            existing = {
                "activity_id": records[0]["id"],
                "activity_json": records[0],
            }
            with (
                patch("src.catalog_sync.is_configured", return_value=True),
                patch("src.catalog_sync.select", return_value=[existing]),
            ):
                plan = build_sync_plan("Rome", "Italy", catalog_path=path)

        self.assertEqual(plan.packaged_count, 2)
        self.assertEqual(plan.remote_count, 1)
        self.assertEqual([activity.id for activity in plan.missing], [records[1]["id"]])

    def test_apply_requires_reviewed_count_and_inserts_only_missing(self) -> None:
        source = Path("data/sample_activities.json")
        records = json.loads(source.read_text(encoding="utf-8"))[:2]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "activities.json"
            path.write_text(json.dumps(records), encoding="utf-8")
            with (
                patch("src.catalog_sync.is_configured", return_value=True),
                patch("src.catalog_sync.select", return_value=[]),
            ):
                plan = build_sync_plan("Rome", "Italy", catalog_path=path)

        with self.assertRaisesRegex(ValueError, "Expected 1"):
            apply_sync_plan(plan, expected_missing=1)
        with patch("src.catalog_sync.insert_many") as insert_many:
            self.assertEqual(apply_sync_plan(plan, expected_missing=2), 2)
            insert_many.assert_called_once()


if __name__ == "__main__":
    unittest.main()
