"""Provider-agnostic LLM layer.

The default provider is Base44: a backend-function webhook wrapping `Core.InvokeLLM`, which
spends Base44 credits. Every adapter returns a parsed dict validated against the caller's JSON
schema, so the rest of the product can rely on schema-valid JSON (see CLAUDE.md, "Stack").

Per the guardrails, this layer only transports reasoning/classification requests. It never
sources activities, durations, logic, counts or compliances — the engine instances those from
`/app/libraries` and the corpus.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict

import httpx
import jsonschema

# Base44's model validator rejects anything outside this set (enumerated by the gateway's own
# error response). `gpt_5` is NOT valid and comes back as HTTP 500.
BASE44_MODELS = (
    'automatic', 'gpt_5_mini', 'gemini_3_flash', 'gpt_5_4', 'gpt_5_6_sol', 'gpt_5_6_luna',
    'gemini_3_1_pro', 'claude_sonnet_4_6', 'claude_opus_4_6', 'claude_opus_4_7',
    'claude_opus_4_8', 'claude-sonnet-5',
)
DEFAULT_MODEL = 'claude_opus_4_8'
DEFAULT_TIMEOUT_SECONDS = 120.0

# Base44 sits behind Cloudflare, which edge-blocks the literal `Python-urllib` User-Agent with
# error 1010 before the function runs. An honest product UA passes; no browser impersonation.
USER_AGENT = 'dc-planner/1.0'

# Base44 reports payload/model validation failures as 500, so 500 is deliberately not retried —
# retrying a bad model or a malformed schema just burns credits.
_RETRY_STATUSES = frozenset({429, 502, 503, 504})
_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = 1.5


class LLMError(RuntimeError):
    """A provider call failed, or returned output that breaks the schema contract."""


def _compose_prompt(system: str | None, user: str | None) -> str:
    """Fold system+user into the single `prompt` string the Base44 gateway accepts."""
    parts = [part.strip() for part in (system, user) if part and part.strip()]
    if not parts:
        raise LLMError('Refusing to call the LLM with an empty prompt.')
    return '\n\n'.join(parts)


class Base44Adapter:
    """Calls the Base44 backend function that wraps `Core.InvokeLLM`.

    Live contract, verified against the deployed function:
      POST {prompt: str (required), schema: object (required), model: str (optional)}
      header `x-shared-secret` (the gateway answers 401 without it)
      -> 200 with the schema-conforming JSON object, returned bare (not wrapped).
    """

    provider = 'base44'

    def __init__(
        self,
        fn_url: str | None = None,
        shared_secret: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.fn_url = fn_url if fn_url is not None else os.getenv('BASE44_FN_URL', '')
        self.shared_secret = (
            shared_secret if shared_secret is not None else os.getenv('BASE44_SHARED_SECRET', '')
        )
        self.model = model or os.getenv('LLM_MODEL') or DEFAULT_MODEL
        self.timeout = (
            timeout
            if timeout is not None
            else float(os.getenv('LLM_TIMEOUT_SECONDS', DEFAULT_TIMEOUT_SECONDS))
        )
        self._transport = transport

    def invoke(
        self,
        system: str = '',
        user: str = '',
        schema: Dict[str, Any] | None = None,
        model: str | None = None,
        prompt: str | None = None,
        validate: bool = True,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """POST to the gateway and return the parsed, schema-validated JSON object."""
        if not self.fn_url:
            raise LLMError('BASE44_FN_URL is not set; cannot reach the Base44 gateway.')
        if not self.shared_secret:
            raise LLMError('BASE44_SHARED_SECRET is not set; the gateway will answer 401.')
        if not isinstance(schema, dict) or not schema:
            raise LLMError(
                'A non-empty JSON schema is required; the gateway rejects calls without one.'
            )

        payload = {
            'prompt': prompt if prompt is not None else _compose_prompt(system, user),
            'schema': schema,
            'model': model or self.model,
        }
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': USER_AGENT,
            'x-shared-secret': self.shared_secret,
        }

        response = self._post_with_retries(payload, headers)

        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise LLMError(f'Base44 returned non-JSON output: {response.text[:400]!r}') from exc

        if not isinstance(data, dict):
            raise LLMError(f'Base44 returned {type(data).__name__}, expected a JSON object.')

        if validate:
            try:
                jsonschema.validate(instance=data, schema=schema)
            except jsonschema.ValidationError as exc:
                raise LLMError(f'Base44 output failed schema validation: {exc.message}') from exc

        return data

    def _post_with_retries(
        self, payload: Dict[str, Any], headers: Dict[str, str]
    ) -> httpx.Response:
        last_error = 'no attempt was made'
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                with httpx.Client(transport=self._transport, timeout=self.timeout) as client:
                    response = client.post(self.fn_url, json=payload, headers=headers)
            except httpx.RequestError as exc:
                last_error = f'{type(exc).__name__}: {exc}'
            else:
                if response.status_code == 200:
                    return response
                last_error = f'HTTP {response.status_code}: {response.text[:400]}'
                if response.status_code not in _RETRY_STATUSES:
                    raise LLMError(f'Base44 call failed ({last_error})')

            if attempt < _MAX_ATTEMPTS:
                time.sleep(_BACKOFF_SECONDS * attempt)

        raise LLMError(f'Base44 call failed after {_MAX_ATTEMPTS} attempts ({last_error})')


class OpenAIAdapter:
    """Alternate provider. Not implemented — Base44 is the default gateway."""

    provider = 'openai'

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key if api_key is not None else os.getenv('OPENAI_API_KEY', '')

    def invoke(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        raise NotImplementedError(
            'OpenAIAdapter is a stub. Set LLM_PROVIDER=base44, or implement this adapter.'
        )


class AnthropicAdapter:
    """Alternate provider. Not implemented — Base44 is the default gateway."""

    provider = 'anthropic'

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key if api_key is not None else os.getenv('ANTHROPIC_API_KEY', '')

    def invoke(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        raise NotImplementedError(
            'AnthropicAdapter is a stub. Set LLM_PROVIDER=base44, or implement this adapter.'
        )


def get_adapter(provider: str | None = None) -> Base44Adapter | OpenAIAdapter | AnthropicAdapter:
    resolved = (provider or os.getenv('LLM_PROVIDER', 'base44')).lower()
    if resolved == 'openai':
        return OpenAIAdapter()
    if resolved == 'anthropic':
        return AnthropicAdapter()
    return Base44Adapter()
