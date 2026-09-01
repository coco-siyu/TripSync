"""Zero-network readiness checks for a TripSync checkout or deployment."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import ValidationError

from src.models import Activity


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
HealthStatus = Literal["pass", "warn", "fail"]


@dataclass(frozen=True)
class HealthCheck:
    """One safe, human-readable readiness result."""

    name: str
    status: HealthStatus
    detail: str


def _runtime_check() -> HealthCheck:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info >= (3, 12):
        return HealthCheck("Python runtime", "pass", version)
    return HealthCheck("Python runtime", "fail", f"{version}; Python 3.12+ is required")


def _catalog_check(repository_root: Path) -> HealthCheck:
    catalog_path = repository_root / "data" / "activities.json"
    try:
        document = json.loads(catalog_path.read_text(encoding="utf-8"))
        if not isinstance(document, list) or not document:
            raise ValueError("catalog must be a non-empty JSON list")
        activities = [Activity.model_validate(item) for item in document]
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as error:
        return HealthCheck("Packaged catalog", "fail", str(error))

    destinations = {
        (activity.city.casefold(), activity.country.casefold())
        for activity in activities
    }
    return HealthCheck(
        "Packaged catalog",
        "pass",
        f"{len(activities)} validated activities across {len(destinations)} destinations",
    )


def _streamlit_config_check(repository_root: Path) -> HealthCheck:
    config_path = repository_root / ".streamlit" / "config.toml"
    try:
        with config_path.open("rb") as config_file:
            config = tomllib.load(config_file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        return HealthCheck("Streamlit config", "fail", str(error))
    if not isinstance(config.get("theme"), dict):
        return HealthCheck("Streamlit config", "fail", "missing [theme] configuration")
    return HealthCheck("Streamlit config", "pass", "valid TOML with theme settings")


def _state_store_check(repository_root: Path, environ: Mapping[str, str]) -> HealthCheck:
    state_directory = Path(
        environ.get("TRIPSYNC_STATE_DIR", str(repository_root / "data"))
    ).expanduser()
    try:
        state_directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix="tripsync-health-",
            suffix=".db",
            dir=state_directory,
        ) as temporary_database:
            connection = sqlite3.connect(temporary_database.name)
            try:
                connection.execute("SELECT 1").fetchone()
            finally:
                connection.close()
    except (OSError, sqlite3.Error) as error:
        return HealthCheck("Local state store", "fail", str(error))
    return HealthCheck("Local state store", "pass", f"writable at {state_directory}")


def _service_configuration_checks(environ: Mapping[str, str]) -> list[HealthCheck]:
    supabase_url = bool(environ.get("SUPABASE_URL", "").strip())
    supabase_secret = bool(environ.get("SUPABASE_SECRET_KEY", "").strip())
    supabase_public = bool(
        environ.get("SUPABASE_PUBLISHABLE_KEY", "").strip()
        or environ.get("SUPABASE_ANON_KEY", "").strip()
    )
    checks: list[HealthCheck] = []
    if (
        (supabase_secret and not supabase_url)
        or (supabase_public and not supabase_url)
        or (supabase_url and not supabase_secret)
    ):
        checks.append(
            HealthCheck(
                "Supabase configuration",
                "fail",
                "partial configuration; shared persistence requires both URL and secret key",
            )
        )
    elif supabase_url and supabase_secret:
        checks.append(
            HealthCheck(
                "Supabase configuration",
                "pass",
                (
                    "shared persistence and account configuration present"
                    if supabase_public
                    else "shared persistence configured; account features are optional and disabled"
                ),
            )
        )
    else:
        checks.append(
            HealthCheck(
                "Supabase configuration",
                "warn",
                "not configured; TripSync will use local persistence",
            )
        )

    checks.append(
        HealthCheck(
            "OpenAI configuration",
            "pass" if environ.get("OPENAI_API_KEY", "").strip() else "warn",
            (
                "API key present"
                if environ.get("OPENAI_API_KEY", "").strip()
                else "not configured; trip stories and semantic enrichment are optional"
            ),
        )
    )
    checks.append(
        HealthCheck(
            "Admin workspace",
            "pass" if environ.get("TRIPSYNC_ADMIN_PASSWORD", "").strip() else "warn",
            (
                "password configured"
                if environ.get("TRIPSYNC_ADMIN_PASSWORD", "").strip()
                else "disabled until TRIPSYNC_ADMIN_PASSWORD is set"
            ),
        )
    )
    return checks


def run_health_checks(
    repository_root: Path = REPOSITORY_ROOT,
    environ: Mapping[str, str] | None = None,
) -> list[HealthCheck]:
    """Run deterministic checks without contacting Supabase or OpenAI."""

    active_environment = os.environ if environ is None else environ
    return [
        _runtime_check(),
        _catalog_check(repository_root),
        _streamlit_config_check(repository_root),
        _state_store_check(repository_root, active_environment),
        *_service_configuration_checks(active_environment),
    ]


def health_exit_code(checks: Sequence[HealthCheck]) -> int:
    """Return a non-zero process status only for failed readiness checks."""

    return 1 if any(check.status == "fail" for check in checks) else 0


def main() -> int:
    load_dotenv()
    checks = run_health_checks()
    labels = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}
    for check in checks:
        print(f"[{labels[check.status]}] {check.name}: {check.detail}")
    return health_exit_code(checks)


if __name__ == "__main__":
    raise SystemExit(main())
