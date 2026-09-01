"""TripSync test suite with focused logging for expected failure scenarios."""

from __future__ import annotations

import logging
import os
import tempfile
import warnings


_TEST_STATE_DIRECTORY = tempfile.TemporaryDirectory(prefix="tripsync-tests-")
os.environ["TRIPSYNC_STATE_DIR"] = _TEST_STATE_DIRECTORY.name

# Application modules load `.env` during import. Predefining these values keeps
# the test process offline unless an individual test explicitly patches them.
for variable_name in (
    "OPENAI_API_KEY",
    "SUPABASE_URL",
    "SUPABASE_SECRET_KEY",
    "SUPABASE_PUBLISHABLE_KEY",
    "SUPABASE_ANON_KEY",
    "TRIPSYNC_ADMIN_PASSWORD",
):
    os.environ[variable_name] = ""


def configure_test_output() -> None:
    """Hide logs deliberately triggered by tests, without hiding test failures."""

    # Streamlit can configure these loggers during import, so test modules call
    # this once more after importing its test harness.
    for logger_name in (
        "streamlit.runtime.caching.cache_data_api",
        "streamlit.runtime.scriptrunner_utils.script_run_context",
    ):
        logging.getLogger(logger_name).disabled = True

    logging.getLogger("src.auth").disabled = True
    warnings.filterwarnings(
        "ignore",
        category=DeprecationWarning,
        module=r"supabase(?:\..*)?",
    )


configure_test_output()
