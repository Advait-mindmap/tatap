"""Shared test fixtures.

Loads `.env` into the process so the live integration tests can reach the real gateway.
Values already set in the environment win, so CI/Railway variables are never overridden.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv(REPO_ROOT / '.env')


# ---------------------------------------------------------------------------------------------
# Usage caps and durable storage are DEPLOYMENT concerns, and both keep state that outlives a
# single test. Left at their production defaults they would make the suite order-dependent and
# then permanently red: the daily counters accumulate in a shared database, so the run that
# crossed the cap would fail every test after it, on that day, forever.
#
# So the suite runs uncapped and on a throwaway database by default. The tests that exercise the
# caps and the store supply their own limits and their own session factory, which is also the
# honest way round — a cap test that depends on ambient configuration is not testing the cap.
# ---------------------------------------------------------------------------------------------
import tempfile

os.environ.setdefault('LLM_DAILY_CALL_CAP', '0')
os.environ.setdefault('RUNS_PER_CLIENT_DAILY', '0')
os.environ.setdefault(
    'DATABASE_URL',
    'sqlite:///' + str(Path(tempfile.gettempdir()) / 'dc_planner_tests.db'),
)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_runs():
    """Every test starts with no runs in memory or in storage."""
    from backend.app.simulator import registry

    registry.reset()
    yield
    registry.reset()
