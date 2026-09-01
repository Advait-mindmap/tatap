"""Intake tests (mocked gateway).

These pin the guardrails: a citation must really be in the source, a low-confidence field
becomes a question rather than a value, and a missing required field is never silently absent.
The live extraction against sample_raw_brief.md is in test_intake_live.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.intake import (
    REQUIRED_FIELDS,
    build_result,
    extract_brief,
    normalise_mode,
    normalise_tier,
    quote_is_grounded,
)
from backend.app.llm import LLMError
from backend.app.main import app
from backend.app.schemas import RawBrief

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_BRIEF = (REPO_ROOT / 'sample_raw_brief.md').read_text(encoding='utf-8')


class FakeAdapter:
    """Stands in for the Base44 gateway. Records what it was asked."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def invoke(self, system='', user='', schema=None, **kwargs):
        self.calls.append({'system': system, 'user': user, 'schema': schema})
        return self.response


def good_response():
    """A response whose quotes are all genuinely in sample_raw_brief.md."""
    return {
        'fields': [
            {'name': 'city', 'value': 'Navi Mumbai', 'confidence': 0.95,
             'quote': 'a client in Navi Mumbai'},
            {'name': 'tier', 'value': 'III', 'confidence': 0.95,
             'quote': 'Uptime Tier III'},
            {'name': 'it_load_mw', 'value': '20', 'confidence': 0.95,
             'quote': 'Target IT load is 20 MW'},
            {'name': 'redundancy_topology', 'value': 'N+1', 'confidence': 0.9,
             'quote': 'N+1 on the\nelectrical and cooling'},
            {'name': 'site_context', 'value': 'greenfield', 'confidence': 0.9,
             'quote': 'greenfield plot'},
            {'name': 'phasing', 'value': 'phased-by-hall', 'confidence': 0.85,
             'quote': 'Client wants phased handover, hall by hall'},
        ],
        'delivery_modes': [
            {'discipline': 'civil', 'mode': 'self-perform', 'confidence': 0.95,
             'quote': 'we self-perform civil and structure'},
            {'discipline': 'gensets', 'mode': 'owner-furnished', 'confidence': 0.95,
             'quote': "Gensets are owner-furnished under the client's existing"},
            {'discipline': 'fire', 'mode': 'subcontract', 'confidence': 0.9,
             'quote': 'Fire suppression and BMS are\nsubcontracted'},
        ],
        'questions': [
            {'field': 'client', 'question': 'What is the client organisation name?',
             'why_needed': 'The brief says "a client" without naming them.'},
        ],
        'conflicts': [],
        'overall_confidence': 0.9,
    }


def run(response, text=SAMPLE_BRIEF, threshold=0.7):
    return build_result(response, RawBrief(text=text), threshold)


# ----------------------------------------------------------------- quote grounding


def test_quote_grounding_accepts_a_real_span():
    assert quote_is_grounded('Target IT load is 20 MW', SAMPLE_BRIEF)


def test_quote_grounding_survives_rewrapped_whitespace():
    """The model may re-wrap lines; that must not invalidate a genuine citation."""
    assert quote_is_grounded('N+1 on the electrical and cooling', SAMPLE_BRIEF)


def test_quote_grounding_rejects_a_fabricated_span():
    assert not quote_is_grounded('Target IT load is 45 MW', SAMPLE_BRIEF)
    assert not quote_is_grounded('The client is Tata Communications', SAMPLE_BRIEF)


def test_quote_grounding_rejects_trivially_short_quotes():
    """A two-word fragment matches by luck and proves nothing."""
    assert not quote_is_grounded('the', SAMPLE_BRIEF)


def test_ungrounded_field_is_discarded_and_asked_instead():
    """A fabricated citation means the model reconstructed rather than read."""
    response = good_response()
    response['fields'].append(
        {'name': 'client', 'value': 'Tata Communications', 'confidence': 0.99,
         'quote': 'the client is Tata Communications'}
    )
    result = run(response)

    assert result.brief.client is None, 'ungrounded value must not reach the brief'
    assert 'client' not in result.field_provenance
    assert any('DISCARDED client' in w for w in result.warnings)
    assert any(q.field == 'client' for q in result.questions)


# --------------------------------------------------------------- confidence gating


def test_low_confidence_field_becomes_a_question_not_a_value():
    response = good_response()
    for field in response['fields']:
        if field['name'] == 'tier':
            field['confidence'] = 0.4
    result = run(response, threshold=0.7)

    assert result.brief.tier is None
    assert 'tier' in result.unresolved_fields
    question = next(q for q in result.questions if q.field == 'tier')
    assert question.blocking is True
    assert '0.40' in question.why_needed


def test_confidence_threshold_is_configurable():
    response = good_response()
    for field in response['fields']:
        if field['name'] == 'phasing':
            field['confidence'] = 0.85
    assert run(response, threshold=0.7).brief.phasing == 'phased-by-hall'
    assert run(response, threshold=0.9).brief.phasing is None


# ------------------------------------------------------------------- extraction


def test_extracts_tier_load_and_city_with_provenance():
    result = run(good_response())

    assert result.brief.tier == 'III'
    assert result.brief.it_load_mw == 20.0
    assert result.brief.city == 'Navi Mumbai'

    for field in ('tier', 'it_load_mw', 'city'):
        prov = result.field_provenance[field]
        assert prov.grounded is True
        assert quote_is_grounded(prov.quote, SAMPLE_BRIEF)


def test_extracts_gensets_as_owner_furnished():
    """OFE moves control of the delivery date to the client - not the same as subcontracting."""
    result = run(good_response())
    assert result.brief.delivery_mode_by_discipline['gensets'] == 'owner-furnished'
    assert result.field_provenance['delivery_mode.gensets'].grounded is True


def test_every_extracted_field_has_provenance():
    """PRODUCT_SPEC.md §3.1: a citation per field. No value may arrive uncited."""
    result = run(good_response())
    for name, value in result.brief.model_dump().items():
        if name == 'delivery_mode_by_discipline':
            for discipline in value:
                assert f'delivery_mode.{discipline}' in result.field_provenance
        elif value is not None:
            assert name in result.field_provenance, f'{name} has a value but no citation'


def test_missing_required_field_is_always_asked():
    response = good_response()
    response['fields'] = [f for f in response['fields'] if f['name'] != 'it_load_mw']
    result = run(response)

    assert result.brief.it_load_mw is None
    question = next(q for q in result.questions if q.field == 'it_load_mw')
    assert question.blocking is True
    assert result.is_complete is False


def test_model_questions_are_carried_through():
    result = run(good_response())
    assert any(q.field == 'client' for q in result.questions)
    assert 'client' in result.unresolved_fields


def test_no_duplicate_questions_for_one_field():
    response = good_response()
    response['questions'].append(
        {'field': 'client', 'question': 'Who is the client?', 'why_needed': 'again'}
    )
    result = run(response)
    assert [q.field for q in result.questions].count('client') == 1


def test_a_field_that_was_extracted_is_not_also_asked():
    response = good_response()
    response['questions'].append(
        {'field': 'tier', 'question': 'What tier?', 'why_needed': 'spurious'}
    )
    result = run(response)
    assert result.brief.tier == 'III'
    assert not any(q.field == 'tier' for q in result.questions)


def test_conflicts_are_flagged_not_resolved():
    response = good_response()
    response['conflicts'] = ['Brief says Tier III in one place and Tier IV in another.']
    result = run(response)
    assert result.flagged_conflicts == response['conflicts']
    assert any('flagged rather than resolved' in w for w in result.warnings)


def test_unparseable_value_is_discarded_and_asked():
    response = good_response()
    for field in response['fields']:
        if field['name'] == 'it_load_mw':
            field['value'] = 'quite a lot'
    result = run(response)
    assert result.brief.it_load_mw is None
    assert any('DISCARDED it_load_mw' in w for w in result.warnings)


# ------------------------------------------------------------------ normalisation


@pytest.mark.parametrize('raw,expected', [
    ('III', 'III'), ('Tier III', 'III'), ('tier iii', 'III'), ('3', 'III'), ('IV', 'IV'),
])
def test_tier_normalisation(raw, expected):
    assert normalise_tier(raw) == expected


@pytest.mark.parametrize('raw,expected', [
    ('OFE', 'owner-furnished'), ('owner furnished', 'owner-furnished'),
    ('Owner-Furnished', 'owner-furnished'), ('client supplied', 'owner-furnished'),
    ('self-perform', 'self-perform'), ('subcontracted', 'subcontract'), ('turnkey', 'turnkey'),
])
def test_delivery_mode_normalisation(raw, expected):
    assert normalise_mode(raw) == expected


def test_unrecognised_delivery_mode_is_discarded():
    response = good_response()
    response['delivery_modes'].append(
        {'discipline': 'bms', 'mode': 'somehow', 'confidence': 0.9,
         'quote': 'Fire suppression and BMS are'}
    )
    result = run(response)
    assert 'bms' not in result.brief.delivery_mode_by_discipline


# ------------------------------------------------------------------------ wiring


def test_extract_brief_sends_role_boundaries_and_schema_to_the_gateway():
    adapter = FakeAdapter(good_response())
    extract_brief(SAMPLE_BRIEF, adapter=adapter)

    call = adapter.calls[0]
    assert 'senior data centre delivery planner' in call['system']
    assert 'EXTRACT ONLY WHAT THE TEXT STATES' in call['system']
    assert 'VERBATIM' in call['system']
    assert SAMPLE_BRIEF in call['user']
    assert call['schema']['required'] == ['fields', 'delivery_modes', 'questions',
                                          'overall_confidence']


def test_extract_brief_accepts_a_plain_string_or_a_rawbrief():
    adapter = FakeAdapter(good_response())
    assert extract_brief(SAMPLE_BRIEF, adapter=adapter).brief.tier == 'III'
    result = extract_brief(
        RawBrief(text=SAMPLE_BRIEF, source_ref='rfp.pdf', attachments=['rfp.pdf']),
        adapter=adapter,
    )
    assert result.raw_brief_ref == 'rfp.pdf'
    assert result.attachments == ['rfp.pdf']


def test_empty_brief_is_rejected():
    with pytest.raises(ValueError, match='empty brief'):
        extract_brief('   ', adapter=FakeAdapter(good_response()))


# ---------------------------------------------------------------------- endpoint


def test_intake_endpoint_returns_brief_questions_and_provenance(monkeypatch):
    monkeypatch.setattr(
        'backend.app.main.extract_brief',
        # The endpoint now hands extract_brief a budget-guarded adapter, so accept it.
        lambda raw, adapter=None: build_result(good_response(), raw, 0.7),
    )
    response = TestClient(app).post('/intake', json={'text': SAMPLE_BRIEF})

    assert response.status_code == 200
    body = response.json()
    assert body['brief']['tier'] == 'III'
    assert body['brief']['it_load_mw'] == 20.0
    assert body['brief']['delivery_mode_by_discipline']['gensets'] == 'owner-furnished'
    assert body['field_provenance']['tier']['quote'] == 'Uptime Tier III'
    assert any(q['field'] == 'client' for q in body['questions'])


def test_intake_endpoint_rejects_empty_text():
    assert TestClient(app).post('/intake', json={'text': ''}).status_code == 422


def test_intake_endpoint_reports_provider_failure_as_502(monkeypatch):
    def boom(raw, adapter=None):
        raise LLMError('gateway down')

    monkeypatch.setattr('backend.app.main.extract_brief', boom)
    response = TestClient(app).post('/intake', json={'text': SAMPLE_BRIEF})
    assert response.status_code == 502
    assert 'gateway down' in response.json()['detail']


def test_is_complete_and_can_proceed_are_distinct():
    """A brief can be simulatable while still missing non-blocking detail."""
    response = good_response()  # leaves `client` outstanding, which is not simulation-critical
    result = run(response)

    assert result.questions, 'expected the unnamed client to be asked'
    assert result.is_complete is False, 'questions outstanding means not fully extracted'
    assert result.can_proceed is True, 'tier/load/city are all present, so simulation can start'
    assert result.blocking_questions == []


def test_a_missing_required_field_blocks_proceeding():
    response = good_response()
    response['fields'] = [f for f in response['fields'] if f['name'] != 'tier']
    result = run(response)

    assert result.can_proceed is False
    assert [q.field for q in result.blocking_questions] == ['tier']
