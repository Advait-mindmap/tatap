"""Load `.env` into the process at import time.

The app reads its configuration from environment variables, which is right for deployment -
Railway injects them - but it meant that running the API locally picked up nothing, because
only the test suite was loading `.env`. Starting uvicorn by hand gave a server that answered
/health and then failed every intake with "BASE44_FN_URL is not set". Configuration that only
works under pytest is not configuration.

Values already present in the environment always win, so a real deployment is never overridden
by a stray file, and `LLM_PROVIDER=stub uvicorn ...` still does what it says.
"""

from __future__ import annotations

import os
from pathlib import Path

#: repo root: backend/app/env.py -> backend/app -> backend -> repo
REPO_ROOT = Path(__file__).resolve().parents[2]


def load_env_file(path: Path | None = None) -> int:
    """Set any variable in `.env` that is not already in the environment. Returns how many."""
    target = path or REPO_ROOT / '.env'
    if not target.is_file():
        return 0

    loaded = 0
    for raw_line in target.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded
