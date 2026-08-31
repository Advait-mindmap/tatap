"""Live per-stage reasoning against the real Base44 gateway.

The mocked tests prove the guardrails reject bad input. Only this one shows whether a real model,
given a real closed vocabulary and real retrieved precedent, stays inside it — and whether it
discloses its reliance on unverified library data instead of writing confidently over it.

Makes real calls and spends Base44 credits. Marked `live`; skipped without credentials.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.models import Base
from backend.app.rag import CorpusKind, ingest_document, ingest_seed_corpus
from backend.app.reasoning import (
    UNVERIFIED_CONFIDENCE_CAP,
    gather_stage_libraries,
    reason_stage,
)

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (os.getenv('BASE44_FN_URL') and os.getenv('BASE44_SHARED_SECRET')),
        reason='BASE44_FN_URL / BASE44_SHARED_SECRET not set; skipping live reasoning tests.',
    ),
]

STAGE = 'mep_power'
BRIEF = {
    'project_name': 'POC DC - Navi Mumbai',
    'city': 'Navi Mumbai',
    'site_context': 'greenfield',
    'tier': 'III',
    'redundancy_topology': 'N+1',
    'it_load_mw': 20.0,
    'scope': 'turnkey',
    'power_position': 'existing 33 kV feeder on east boundary; HT substation on our scope',
    'delivery_mode_by_discipline': {'electrical': 'turnkey', 'gensets': 'owner-furnished'},
}


@pytest.fixture(scope='module')
def session():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        ingest_seed_corpus(s)
        # One real-execution document, so grounding can actually be satisfied.
        ingest_document(
            s,
            title='Delivered DC - Navi Mumbai power train',
            source='client-history',
            kind=CorpusKind.REAL_EXECUTION,
            content=(
                'On the delivered Navi Mumbai data centre the HV transformer and MV switchgear '
                'drove the energisation date. The power train sequence ran transformer plinth, '
                'transformer placement, switchgear installation, then UPS and busway. CEIG '
                'approval gated energisation and PESO licensing gated the diesel storage.'
            ),
            project_name='Delivered DC', city='Navi Mumbai', tier='III',
        )
        s.commit()
        yield s


@pytest.fixture(scope='module')
def result(session):
    """One live reasoning run shared by the assertions, to spend credits once."""
    return reason_stage(STAGE, BRIEF, session=session)


def test_live_selects_only_library_identifiers(result):
    """The closed vocabulary must hold against a real model, not just a mocked one."""
    libs = gather_stage_libraries(STAGE, BRIEF['city'])
    allowed_frags = {f['id'] for f in libs['fragnets']}
    allowed_gates = {g['id'] for g in libs['gates']}
    allowed_leads = {e['id'] for e in libs['long_lead']}

    assert {p.fragnet_id for p in result.packages} <= allowed_frags
    assert {g.gate_id for g in result.gates} <= allowed_gates
    assert {l.lead_id for l in result.long_lead} <= allowed_leads


def test_live_produced_some_reasoning(result):
    assert result.packages or result.decision_points, (
        f'reasoner produced nothing usable. warnings={result.warnings} '
        f'rejected={result.rejected}'
    )


def test_live_cites_only_supplied_sources(result):
    """Any citation we did not supply would have been discarded; nothing accepted may be loose."""
    allowed = set(result.retrieved_source_ids)
    libs = gather_stage_libraries(STAGE, BRIEF['city'])
    allowed |= {e['id'] for group in libs.values() for e in group}

    for element in [*result.packages, *result.gates, *result.long_lead]:
        assert set(element.sources) <= allowed, f'{element} cites something never supplied'


def test_live_every_accepted_element_has_a_trail_entry(result):
    """SIMULATION_AND_REASONING.md §5: every element is auditable."""
    accepted = (
        {p.fragnet_id for p in result.packages}
        | {g.gate_id for g in result.gates}
        | {l.lead_id for l in result.long_lead}
    )
    assert {t.ref_id for t in result.trail} == accepted
    for entry in result.trail:
        assert entry.why.strip(), f'{entry.ref_id} has no reasoning'


def test_live_discloses_reliance_on_unverified_library_data(result):
    """The anti-laundering rule, against a real model.

    The whole seed library is unverified invented data, so any selection must come back capped,
    named and flagged rather than asserted confidently.
    """
    if not result.trail:
        pytest.skip('no elements were accepted, so there is nothing to disclose')

    assert result.rests_on_unverified_data is True

    for entry in result.trail:
        assert entry.unverified_dependencies, f'{entry.ref_id} did not name its unverified basis'
        assert entry.confidence <= UNVERIFIED_CONFIDENCE_CAP, (
            f'{entry.ref_id} kept confidence {entry.confidence} on unverified data'
        )
        assert entry.hitl_tier == 'tier_2'

    flags = [f for f in result.flags if f.kind == 'unverified_library_data']
    assert flags, 'no Tier-2 flag raised despite resting on unverified data'


def test_live_stated_confidence_is_preserved_for_audit(result):
    """The cap must not erase what the model actually claimed."""
    if not result.trail:
        pytest.skip('nothing accepted')
    for entry in result.trail:
        assert entry.stated_confidence is not None


def test_live_emits_no_activities_or_durations(result):
    """CLAUDE.md rule 2: the engine instances those, never the model.

    The schema gives it nowhere to put them; this asserts the result really is free of them.
    """
    payload = result.model_dump()
    for key in ('activities', 'duration_days', 'logic', 'lag', 'calendar'):
        assert key not in payload, f'reasoning output carries {key}, which is the engine\'s job'


def test_live_is_grounded_in_the_real_execution_document(result):
    """A real-execution document was seeded, so retrieval should reach it."""
    assert result.grounded_in_real_execution is True
    assert result.retrieved_source_ids


def test_live_records_versions_for_traceability(result):
    assert result.library_version and result.corpus_version and result.prompt_version
