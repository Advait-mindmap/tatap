"""Live integration tests against the real Base44 gateway.

These make real HTTP calls and spend Base44 credits, so they are marked `live` and kept small.
Skipped automatically when BASE44_FN_URL / BASE44_SHARED_SECRET are absent (CI without secrets).
Deselect with:  pytest -m "not live"

This is the test that would have caught the original blocker: the Cloudflare 1010 edge block
returned 403 before the function ran, and no mocked test can see that.
"""

from __future__ import annotations

import os

import jsonschema
import pytest

from backend.app.llm import DEFAULT_MODEL, Base44Adapter, LLMError

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (os.getenv('BASE44_FN_URL') and os.getenv('BASE44_SHARED_SECRET')),
        reason='BASE44_FN_URL / BASE44_SHARED_SECRET not set; skipping live gateway tests.',
    ),
]

ECHO_SCHEMA = {
    'type': 'object',
    'properties': {'msg': {'type': 'string'}},
    'required': ['msg'],
}


def test_live_gateway_returns_schema_valid_json():
    """200 from the real endpoint, parsed, and valid against the schema we asked for."""
    result = Base44Adapter().invoke(
        system='You are a test harness. Answer with JSON only.',
        user='Set the field msg to exactly: hello',
        schema=ECHO_SCHEMA,
    )

    # invoke() validates internally; assert explicitly so the guarantee is visible in the test.
    jsonschema.validate(instance=result, schema=ECHO_SCHEMA)
    assert isinstance(result, dict)
    assert isinstance(result['msg'], str)
    assert 'hello' in result['msg'].lower()


def test_live_gateway_honours_nested_schema_and_reasons_in_domain():
    """Nested arrays/objects survive the round trip, and the model answers as a DC planner."""
    schema = {
        'type': 'object',
        'properties': {
            'approvals': {
                'type': 'array',
                'minItems': 1,
                'items': {
                    'type': 'object',
                    'properties': {
                        'name': {'type': 'string'},
                        'authority': {'type': 'string'},
                    },
                    'required': ['name', 'authority'],
                },
            },
            'confidence': {'type': 'number'},
        },
        'required': ['approvals', 'confidence'],
    }

    result = Base44Adapter().invoke(
        system=(
            'ROLE: You are a senior data centre delivery planner in India with 20+ years of '
            'real project execution. Answer with JSON only.'
        ),
        user=(
            'List the statutory approvals that gate energisation of an HT installation for a '
            'data centre in Maharashtra, with the authority for each.'
        ),
        schema=schema,
    )

    jsonschema.validate(instance=result, schema=schema)
    assert len(result['approvals']) >= 1
    # The electrical inspectorate gates energisation (DOMAIN_KNOWLEDGE.md §4, §5).
    blob = ' '.join(f"{a['name']} {a['authority']}" for a in result['approvals']).lower()
    assert 'ceig' in blob or 'electrical inspector' in blob


def test_live_gateway_rejects_a_bad_shared_secret():
    """Confirms the secret is actually enforced by the deployed function (401, not a silent pass)."""
    adapter = Base44Adapter(shared_secret='definitely-not-the-secret')
    with pytest.raises(LLMError, match='HTTP 401'):
        adapter.invoke(user='Set msg to hello', schema=ECHO_SCHEMA)


def test_live_default_model_is_accepted_by_the_gateway():
    """Guards the regression that started this: LLM_MODEL=gpt_5 was rejected with HTTP 500."""
    result = Base44Adapter(model=DEFAULT_MODEL).invoke(
        user='Set the field msg to exactly: ok', schema=ECHO_SCHEMA
    )
    assert isinstance(result['msg'], str)
