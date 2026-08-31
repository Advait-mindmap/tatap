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
