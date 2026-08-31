"""Manual smoke probe against the live Base44 gateway.

    python backend/tests/base44_live_probe.py

Goes through Base44Adapter so it exercises the same path the product uses. The earlier version
called urllib directly with no User-Agent, which Cloudflare edge-blocked with error 1010 (403)
before the function ever ran — see USER_AGENT in backend/app/llm.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.llm import Base44Adapter, LLMError  # noqa: E402
from backend.tests.conftest import _load_dotenv, REPO_ROOT  # noqa: E402


def main() -> int:
    _load_dotenv(REPO_ROOT / '.env')
    adapter = Base44Adapter()
    print(f'URL   : {adapter.fn_url or "(unset)"}')
    print(f'MODEL : {adapter.model}')
    print(f'SECRET: {"set" if adapter.shared_secret else "MISSING"}')

    try:
        result = adapter.invoke(
            system='You are a test harness. Answer with JSON only.',
            user='Set the field msg to exactly: hello',
            schema={
                'type': 'object',
                'properties': {'msg': {'type': 'string'}},
                'required': ['msg'],
            },
        )
    except LLMError as exc:
        print(f'FAILED: {exc}')
        return 1

    print(f'OK    : {json.dumps(result)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
