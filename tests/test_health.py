"""Tests for the zero-network TripSync readiness command."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.health import health_exit_code, run_health_checks


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class HealthCheckTests(unittest.TestCase):
    def test_repository_is_ready_without_optional_services(self) -> None:
        with tempfile.TemporaryDirectory() as state_directory:
            checks = run_health_checks(
                REPOSITORY_ROOT,
                {"TRIPSYNC_STATE_DIR": state_directory},
            )

        self.assertEqual(health_exit_code(checks), 0)
        self.assertTrue(any(check.name == "Packaged catalog" for check in checks))
        self.assertTrue(any(check.status == "warn" for check in checks))

    def test_partial_supabase_configuration_fails_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as state_directory:
            checks = run_health_checks(
                REPOSITORY_ROOT,
                {
                    "TRIPSYNC_STATE_DIR": state_directory,
                    "SUPABASE_URL": "https://example.supabase.co",
                },
            )

        self.assertEqual(health_exit_code(checks), 1)
        supabase_check = next(
            check for check in checks if check.name == "Supabase configuration"
        )
        self.assertEqual(supabase_check.status, "fail")

    def test_shared_persistence_does_not_require_account_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as state_directory:
            checks = run_health_checks(
                REPOSITORY_ROOT,
                {
                    "TRIPSYNC_STATE_DIR": state_directory,
                    "SUPABASE_URL": "https://example.supabase.co",
                    "SUPABASE_SECRET_KEY": "server-secret",
                },
            )

        self.assertEqual(health_exit_code(checks), 0)
        supabase_check = next(
            check for check in checks if check.name == "Supabase configuration"
        )
        self.assertEqual(supabase_check.status, "pass")
        self.assertIn("account features", supabase_check.detail)
