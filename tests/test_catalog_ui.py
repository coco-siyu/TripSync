"""Tests for defensive candidate-table selection."""

from __future__ import annotations

import unittest

from src.catalog_ui import _selected_candidate


class CatalogUiTests(unittest.TestCase):
    def test_selected_candidate_rejects_stale_or_missing_selection(self) -> None:
        rows = [{"candidate": {"name": "One"}}, {"candidate": {"name": "Two"}}]

        self.assertIsNone(_selected_candidate(rows, []))
        self.assertIsNone(_selected_candidate(rows, [2]))
        self.assertIsNone(_selected_candidate(rows, [0, 1]))
        self.assertEqual(_selected_candidate(rows, [1]), {"name": "Two"})
