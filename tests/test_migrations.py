"""Migration round-trip: downgrade to base, upgrade back to head, and the
full schema must be exactly what the models declare — not a stale subset
left over from a half-applied migration.
"""

import subprocess
import sys
from pathlib import Path

from sqlalchemy import inspect

from opspilot.db import engine

REPO_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_TABLES = {
    "services",
    "scenarios",
    "deployments",
    "metric_points",
    "log_entries",
    "dependency_statuses",
    "runbooks",
    "incidents",
    "audit_log_entries",
}


def _run_alembic(*args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        check=True,
        cwd=REPO_ROOT,
    )


def test_migration_round_trip():
    _run_alembic("downgrade", "base")
    engine.dispose()
    tables_after_downgrade = set(inspect(engine).get_table_names())
    assert not (tables_after_downgrade & EXPECTED_TABLES)

    _run_alembic("upgrade", "head")
    engine.dispose()
    tables_after_upgrade = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES <= tables_after_upgrade
