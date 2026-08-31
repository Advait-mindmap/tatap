"""Unit tests for the provider-agnostic LLM layer. No network: httpx.MockTransport stands in
for the Base44 gateway, mirroring the contract verified live in test_llm_live.py.
"""

import importlib
import json

import httpx
import pytest

from backend.app.llm import (
    BASE44_MODELS,
    DEFAULT_MODEL,
    USER_AGENT,
    AnthropicAdapter,
    Base44Adapter,
    LLMError,
    OpenAIAdapter,
)

FN_URL = 'https://example.base44.app/functions/llmClassify'
SECRET = 'test-secret'
SCHEMA = {
    'type': 'object',
    'properties': {'msg': {'type': 'string'}},
    'required': ['msg'],
}


def make_adapter(handler, **kwargs):
    return Base44Adapter(
        fn_url=FN_URL,
        shared_secret=SECRET,
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def test_provider_factory_defaults_to_base44(monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'base44')
    module = importlib.import_module('backend.app.llm')
    adapter = module.get_adapter()
    assert adapter.__class__.__name__ == 'Base44Adapter'


def test_default_model_is_claude_opus_4_8(monkeypatch):
    monkeypatch.delenv('LLM_MODEL', raising=False)
    assert DEFAULT_MODEL == 'claude_opus_4_8'
    assert Base44Adapter(fn_url=FN_URL, shared_secret=SECRET).model == 'claude_opus_4_8'


def test_posts_gateway_contract_and_returns_parsed_json():
    """prompt+schema+model in the body, secret and honest UA in the headers."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen['url'] = str(request.url)
        seen['headers'] = request.headers
        seen['body'] = json.loads(request.content)
        return httpx.Response(200, json={'msg': 'hello'})

    result = make_adapter(handler).invoke(
        system='You are a senior DC planner.',
        user='Reply with msg=hello.',
        schema=SCHEMA,
    )

    assert result == {'msg': 'hello'}
    assert seen['url'] == FN_URL
    assert seen['headers']['x-shared-secret'] == SECRET
    assert seen['headers']['user-agent'] == USER_AGENT
    assert 'urllib' not in seen['headers']['user-agent']
    # system and user are folded into the single `prompt` the gateway accepts.
    assert seen['body']['prompt'] == 'You are a senior DC planner.\n\nReply with msg=hello.'
    assert seen['body']['schema'] == SCHEMA
    assert seen['body']['model'] == DEFAULT_MODEL
    assert 'system' not in seen['body'] and 'user' not in seen['body']


def test_explicit_model_overrides_the_default():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen['body'] = json.loads(request.content)
        return httpx.Response(200, json={'msg': 'hi'})

    make_adapter(handler).invoke(user='hi', schema=SCHEMA, model='claude_sonnet_4_6')
    assert seen['body']['model'] == 'claude_sonnet_4_6'


def test_schema_violation_raises():
    """The layer's contract is schema-valid JSON, so a wrong-typed field must not pass through."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={'msg': 123})

    with pytest.raises(LLMError, match='schema validation'):
        make_adapter(handler).invoke(user='hi', schema=SCHEMA)


def test_non_json_body_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text='<html>not json</html>')

    with pytest.raises(LLMError, match='non-JSON'):
        make_adapter(handler).invoke(user='hi', schema=SCHEMA)


def test_cloudflare_403_raises_without_retrying():
    """Regression guard for the 1010 edge block: fail loudly, and do not burn credits retrying."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(403, text='error code: 1010')

    with pytest.raises(LLMError, match='HTTP 403'):
        make_adapter(handler).invoke(user='hi', schema=SCHEMA)
    assert len(calls) == 1


def test_invalid_model_500_is_not_retried():
    """Base44 reports validation failures as 500; retrying one just spends credits."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500, json={'error': "Invalid model 'gpt_5'"})

    with pytest.raises(LLMError, match='HTTP 500'):
        make_adapter(handler).invoke(user='hi', schema=SCHEMA)
    assert len(calls) == 1


def test_transient_503_is_retried_then_succeeds(monkeypatch):
    monkeypatch.setattr('backend.app.llm.time.sleep', lambda _: None)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(503, text='upstream busy')
        return httpx.Response(200, json={'msg': 'recovered'})

    assert make_adapter(handler).invoke(user='hi', schema=SCHEMA) == {'msg': 'recovered'}
    assert len(calls) == 3


def test_missing_config_raises_instead_of_stubbing():
    """The old placeholder returned a fake success here; misconfiguration must now be loud."""
    with pytest.raises(LLMError, match='BASE44_FN_URL'):
        Base44Adapter(fn_url='', shared_secret=SECRET).invoke(user='hi', schema=SCHEMA)
    with pytest.raises(LLMError, match='BASE44_SHARED_SECRET'):
        Base44Adapter(fn_url=FN_URL, shared_secret='').invoke(user='hi', schema=SCHEMA)


def test_empty_prompt_and_missing_schema_raise():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - never reached
        return httpx.Response(200, json={'msg': 'x'})

    with pytest.raises(LLMError, match='schema is required'):
        make_adapter(handler).invoke(user='hi', schema=None)
    with pytest.raises(LLMError, match='empty prompt'):
        make_adapter(handler).invoke(system='  ', user='', schema=SCHEMA)


def test_alternate_providers_are_declared_stubs():
    """Better a clear NotImplementedError than a fake response reaching the engine."""
    with pytest.raises(NotImplementedError):
        OpenAIAdapter(api_key='x').invoke(user='hi', schema=SCHEMA)
    with pytest.raises(NotImplementedError):
        AnthropicAdapter(api_key='x').invoke(user='hi', schema=SCHEMA)


# --------------------------------------------------------------------------------------------
# Contract double.
#
# The deployed Base44 function's source lives in the Base44 dashboard and cannot be seen from
# this repo, so there is nothing here to unit-test directly. Instead this handler reproduces the
# endpoint's *observed* behaviour, exactly as documented in docs/BASE44_GATEWAY.md, and the tests
# below assert that our adapter satisfies it. If Base44's real behaviour drifts, the live tests
# in test_llm_live.py are what will catch it — these only pin our side of the contract.
# --------------------------------------------------------------------------------------------


def verified_gateway(request: httpx.Request) -> httpx.Response:
    if request.headers.get('user-agent', '').startswith('Python-urllib'):
        return httpx.Response(403, text='error code: 1010')
    if request.headers.get('x-shared-secret') != SECRET:
        return httpx.Response(401, json={'error': 'Unauthorized'})

    body = json.loads(request.content)
    if not isinstance(body.get('prompt'), str) or not body['prompt']:
        return httpx.Response(400, json={'error': 'Missing or invalid "prompt" (string)'})
    if not isinstance(body.get('schema'), dict) or not body['schema']:
        return httpx.Response(400, json={'error': 'Missing or invalid "schema" (object)'})
    if 'model' in body and body['model'] not in BASE44_MODELS:
        return httpx.Response(500, json={'error': f"Invalid model '{body['model']}'."})

    # 200: the schema-conforming object, bare — no envelope.
    return httpx.Response(200, json={'msg': 'hello'})


def test_adapter_satisfies_the_verified_gateway_contract():
    result = make_adapter(verified_gateway).invoke(
        system='ROLE: senior DC planner.', user='Set msg to hello.', schema=SCHEMA
    )
    assert result == {'msg': 'hello'}


def test_default_model_is_one_the_gateway_accepts():
    """Regression guard: LLM_MODEL=gpt_5 was rejected as an invalid model."""
    assert DEFAULT_MODEL in BASE44_MODELS
    assert 'gpt_5' not in BASE44_MODELS
    assert make_adapter(verified_gateway).invoke(user='hi', schema=SCHEMA) == {'msg': 'hello'}


def test_adapter_user_agent_passes_the_cloudflare_filter():
    assert not USER_AGENT.startswith('Python-urllib')
    assert make_adapter(verified_gateway).invoke(user='hi', schema=SCHEMA) == {'msg': 'hello'}


def test_wrong_secret_is_rejected_by_the_contract():
    adapter = Base44Adapter(
        fn_url=FN_URL, shared_secret='wrong', transport=httpx.MockTransport(verified_gateway)
    )
    with pytest.raises(LLMError, match='HTTP 401'):
        adapter.invoke(user='hi', schema=SCHEMA)


def test_gateway_rejects_the_response_json_schema_key():
    """The live endpoint requires `schema`; `response_json_schema` is not accepted."""
    request = httpx.Request(
        'POST',
        FN_URL,
        json={'prompt': 'hi', 'response_json_schema': SCHEMA},
        headers={'x-shared-secret': SECRET, 'User-Agent': USER_AGENT},
    )
    response = verified_gateway(request)
    assert response.status_code == 400
    assert 'schema' in response.json()['error']
