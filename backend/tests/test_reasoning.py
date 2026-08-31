"""Per-stage expert reasoning loop (mocked gateway).

These pin the three guardrails: the model may only select identifiers we supplied, may only
cite sources we supplied, and may not launder unverified invented library data into confident
reasoning. Live reasoning against the real gateway is in test_reasoning_live.py.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.models import Base
from backend.app.rag import CorpusKind, LexicalHashEmbedder, ingest_document
from backend.app.reasoning import (
    STAGES,
    UNVERIFIED_CONFIDENCE_CAP,
    build_stage_reasoning,
    gather_stage_libraries,
    reason_stage,
    reasoning_schema,
    retrieve_for_stage,
)
from backend.app.reasoning.loop import build_stage_reasoning as _bsr

BRIEF = {
    'project_name': 'POC DC',
    'city': 'Navi Mumbai',
    'tier': 'III',
    'it_load_mw': 20.0,
    'redundancy_topology': 'N+1',
}

STAGE = 'mep_power'


class FakeAdapter:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def invoke(self, system='', user='', schema=None, **kwargs):
        self.calls.append({'system': system, 'user': user, 'schema': schema})
        return self.response


@pytest.fixture
def libs():
    return gather_stage_libraries(STAGE, 'Navi Mumbai')


@pytest.fixture
def hits():
    return [
        {'ref': 'corpus:1#0', 'doc_id': 1, 'chunk_id': 1, 'kind': 'real_execution',
         'score': 0.8, 'verified': False, 'citable_as_precedent': True,
         'text': 'On the delivered project the transformer arrived late.', 'title': 'Project X'},
        {'ref': 'corpus:2#0', 'doc_id': 2, 'chunk_id': 2, 'kind': 'standard',
         'score': 0.6, 'verified': False, 'citable_as_precedent': False,
         'text': 'CEIG approval precedes energisation.', 'title': 'Standards'},
    ]


def good_response():
    return {
        'stage': STAGE,
        'packages': [{
            'fragnet_id': 'frag.mep.power_train',
            'why': 'The power train is the core MEP scope for this stage.',
            'confidence': 0.9,
            'sources': ['corpus:1#0'],
            'predecessors': [],
        }],
        'gates': [{
            'gate_id': 'path.nm.peso_hsd',
            'why': 'Bulk diesel storage for the generators requires PESO licensing.',
            'confidence': 0.85,
            'sources': ['corpus:2#0'],
        }],
        'long_lead': [{
            'lead_id': 'lead.transformer_hv',
            'why': 'The transformer drives energisation and therefore RFS.',
            'confidence': 0.9,
            'sources': ['corpus:1#0'],
        }],
        'decision_points': [{
            'decision_point_id': 'dp.grid_position',
            'why_stuck': 'The brief says the HT substation is on our scope but the grid '
                         'position is not confirmed.',
        }],
        'notes': '',
    }


def run(response, libs, hits, threshold=0.7, grounded=True):
    return build_stage_reasoning(
        response, stage=STAGE, libs=libs, hits=hits, threshold=threshold, grounded=grounded
    )


# ------------------------------------------------------------------ closed vocabulary


def test_the_output_schema_has_nowhere_to_put_an_activity_or_duration():
    """CLAUDE.md rule 2: the model must not emit activities, durations, logic or counts."""
    schema = reasoning_schema()
    flat = str(schema)
    for forbidden in ('duration', 'activities', 'lag', 'logic', 'calendar'):
        assert forbidden not in flat, f'schema exposes a {forbidden} field for the model to fill'
    assert set(schema['properties']) == {
        'stage', 'packages', 'gates', 'long_lead', 'decision_points', 'notes'
    }


def test_gather_stage_libraries_scopes_to_the_stage(libs):
    assert [f['id'] for f in libs['fragnets']] == ['frag.mep.power_train']
    assert [g['id'] for g in libs['gates']] == ['path.nm.peso_hsd']
    # Only long-lead items this stage's fragnets actually consume.
    assert 'lead.transformer_hv' in {e['id'] for e in libs['long_lead']}
    assert 'lead.chiller' not in {e['id'] for e in libs['long_lead']}


def test_invented_fragnet_id_is_rejected_not_instanced(libs, hits):
    """An id we never offered is an invented activity by another name."""
    response = good_response()
    response['packages'].append({
        'fragnet_id': 'frag.mep.invented_switchroom',
        'why': 'Sounds plausible.', 'confidence': 0.95, 'sources': ['corpus:1#0'],
    })
    result = run(response, libs, hits)

    assert [p.fragnet_id for p in result.packages] == ['frag.mep.power_train']
    rejection = next(r for r in result.rejected if r['id'] == 'frag.mep.invented_switchroom')
    assert 'invented it' in rejection['reason']


def test_invented_gate_and_lead_ids_are_rejected(libs, hits):
    response = good_response()
    response['gates'].append({'gate_id': 'path.nm.made_up', 'why': 'x', 'confidence': 0.9,
                              'sources': ['corpus:1#0']})
    response['long_lead'].append({'lead_id': 'lead.unobtainium', 'why': 'x', 'confidence': 0.9,
                                  'sources': ['corpus:1#0']})
    result = run(response, libs, hits)

    assert [g.gate_id for g in result.gates] == ['path.nm.peso_hsd']
    assert [l.lead_id for l in result.long_lead] == ['lead.transformer_hv']
    assert {r['id'] for r in result.rejected} == {'path.nm.made_up', 'lead.unobtainium'}


def test_decision_point_outside_the_library_is_rejected(libs, hits):
    response = good_response()
    response['decision_points'].append(
        {'decision_point_id': 'dp.invented_fork', 'why_stuck': 'x'}
    )
    result = run(response, libs, hits)
    assert [d.decision_point_id for d in result.decision_points] == ['dp.grid_position']
    assert any(r['id'] == 'dp.invented_fork' for r in result.rejected)


# ------------------------------------------------------------------- citation grounding


def test_fabricated_citation_discards_the_whole_element(libs, hits):
    """A composed citation is a fabricated precedent - worse than none, so the element goes."""
    response = good_response()
    response['packages'][0]['sources'] = ['corpus:99#7']
    result = run(response, libs, hits)

    assert result.packages == []
    rejection = next(r for r in result.rejected if r['id'] == 'frag.mep.power_train')
    assert 'fabricated citation' in rejection['reason']


def test_partially_fabricated_citation_also_discards(libs, hits):
    response = good_response()
    response['packages'][0]['sources'] = ['corpus:1#0', 'corpus:404#0']
    result = run(response, libs, hits)
    assert result.packages == []


def test_library_ids_are_citable(libs, hits):
    """Citing a library entry we supplied is legitimate, not a fabrication."""
    response = good_response()
    response['packages'][0]['sources'] = ['frag.mep.power_train']
    result = run(response, libs, hits)
    assert result.packages and result.packages[0].sources == ['frag.mep.power_train']


def test_every_accepted_element_produces_a_trail_entry(libs, hits):
    """SIMULATION_AND_REASONING.md §5: every node carries a trail entry."""
    result = run(good_response(), libs, hits)
    refs = {t.ref_id for t in result.trail}
    assert refs == {'frag.mep.power_train', 'path.nm.peso_hsd', 'lead.transformer_hv'}
    for entry in result.trail:
        assert entry.why and entry.stage == STAGE and entry.sources


# -------------------------------------------------- unverified data must not be laundered


def test_unverified_library_data_caps_confidence(libs, hits):
    """The anti-laundering rule: invented numbers cannot re-emerge as confident conclusions."""
    result = run(good_response(), libs, hits)
    package = result.packages[0]

    # frag.mep.power_train is model_generated and unverified in the seed library.
    assert package.confidence == 0.9, 'the model stated 0.9'
    assert package.effective_confidence == UNVERIFIED_CONFIDENCE_CAP
    assert package.unverified_dependencies == ['frag.mep.power_train']


def test_unverified_dependency_is_named_in_the_trail(libs, hits):
    result = run(good_response(), libs, hits)
    entry = next(t for t in result.trail if t.ref_id == 'frag.mep.power_train')

    assert entry.unverified_dependencies == ['frag.mep.power_train']
    assert entry.stated_confidence == 0.9
    assert entry.confidence == UNVERIFIED_CONFIDENCE_CAP
    assert entry.hitl_tier == 'tier_2'
    assert result.rests_on_unverified_data is True


def test_unverified_reliance_raises_a_tier_2_flag(libs, hits):
    result = run(good_response(), libs, hits)
    flags = [f for f in result.flags if f.kind == 'unverified_library_data']
    assert flags
    assert all(f.hitl_tier == 'tier_2' for f in flags)
    assert any('no human has checked' in f.message for f in flags)


def test_the_cap_sits_below_the_confidence_threshold():
    """So a conclusion built on invented data can never look better than provisional."""
    from backend.app.reasoning.loop import conf_threshold
    assert UNVERIFIED_CONFIDENCE_CAP < conf_threshold()


def test_verified_entry_is_not_capped(libs, hits):
    """Once a human verifies the data in admin, the reasoning may stand at full confidence."""
    def as_verified(entry):
        return {**entry, 'provenance': {**entry['provenance'],
                                        'verification_status': 'verified'}}

    # Every entry the response selects must be verified, or the result still rests on
    # unverified data via the gate and the long-lead item.
    patched = {
        **libs,
        'fragnets': [as_verified(e) for e in libs['fragnets']],
        'gates': [as_verified(e) for e in libs['gates']],
        'long_lead': [as_verified(e) for e in libs['long_lead']],
    }

    result = run(good_response(), patched, hits)
    assert result.packages[0].effective_confidence == 0.9
    assert result.packages[0].unverified_dependencies == []
    assert result.rests_on_unverified_data is False
    assert all(t.hitl_tier == 'tier_3' for t in result.trail)
    assert not [f for f in result.flags if f.kind == 'unverified_library_data']


def test_unverified_cap_does_not_by_itself_halt_the_branch(libs, hits):
    """§4: soft uncertainty is Tier-2, not a full stop. Only genuine forks halt."""
    response = good_response()
    response['decision_points'] = []
    result = run(response, libs, hits)

    assert result.rests_on_unverified_data is True
    assert result.is_halted is False, 'capped confidence is a data-quality issue, not a fork'


# -------------------------------------------------------------------- decision points


def test_curated_decision_point_uses_library_text_not_model_text(libs, hits):
    """The question and options are the curated library's, so the fork is stated consistently."""
    result = run(good_response(), libs, hits)
    dp = next(d for d in result.decision_points if d.decision_point_id == 'dp.grid_position')

    library_entry = next(d for d in libs['decision_points'] if d['id'] == 'dp.grid_position')
    assert dp.question == library_entry['question']
    assert dp.options == library_entry['options']
    assert dp.impact == library_entry['impact']
    assert dp.blocking is True and dp.detection == 'curated'
    assert result.is_halted is True


def test_low_stated_confidence_raises_a_dynamic_decision_point(libs, hits):
    """§4: confidence below CONF_THRESHOLD raises a decision point rather than guessing."""
    response = good_response()
    response['decision_points'] = []
    response['packages'][0]['confidence'] = 0.3
    result = run(response, libs, hits, threshold=0.7)

    dyn = [d for d in result.decision_points if d.detection == 'dynamic']
    assert len(dyn) == 1
    assert dyn[0].decision_point_id == 'dyn.low_confidence.frag.mep.power_train'
    assert dyn[0].blocking is True
    assert '0.30' in dyn[0].why_stuck
    assert result.is_halted is True


def test_dynamic_detection_uses_stated_not_capped_confidence(libs, hits):
    """Otherwise every branch would halt on seed data, asking a question no planner can answer."""
    response = good_response()
    response['decision_points'] = []
    result = run(response, libs, hits, threshold=0.7)

    assert result.packages[0].effective_confidence < 0.7, 'capped below threshold'
    assert [d for d in result.decision_points if d.detection == 'dynamic'] == []


def test_duplicate_decision_points_are_raised_once(libs, hits):
    response = good_response()
    response['decision_points'].append(
        {'decision_point_id': 'dp.grid_position', 'why_stuck': 'again'}
    )
    result = run(response, libs, hits)
    assert len(result.decision_points) == 1


# ---------------------------------------------------------------------- grounding


def test_absence_of_real_precedent_is_warned(libs, hits):
    result = run(good_response(), libs, hits, grounded=False)
    assert result.grounded_in_real_execution is False
    assert any('NOT GROUNDED IN REAL EXECUTION' in w for w in result.warnings)


def test_versions_are_recorded_for_traceability(libs, hits):
    """A plan must be traceable to the library and prompt that produced it."""
    result = run(good_response(), libs, hits)
    assert result.library_version and result.corpus_version and result.prompt_version


def test_retrieval_over_a_real_corpus_feeds_the_loop():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    embedder = LexicalHashEmbedder()
    with Session(engine) as session:
        ingest_document(
            session, title='Delivered project', source='hist',
            kind=CorpusKind.REAL_EXECUTION,
            content='On this delivered data centre the HV transformer drove energisation and '
                    'the mep power train sequence followed the switchgear delivery.',
            embedder=embedder,
        )
        session.commit()
        hits, warnings, grounded = retrieve_for_stage(session, 'mep_power', BRIEF)

    assert hits and grounded is True
    assert all(h['ref'].startswith('corpus:') for h in hits)


def test_no_session_means_no_precedent_and_says_so():
    hits, warnings, grounded = retrieve_for_stage(None, 'mep_power', BRIEF)
    assert hits == [] and grounded is False
    assert any('no retrieved precedent' in w for w in warnings)


# ------------------------------------------------------------------------- wiring


def test_reason_stage_sends_boundaries_and_closed_vocabulary(libs):
    adapter = FakeAdapter(good_response())
    reason_stage(STAGE, BRIEF, adapter=adapter)
    call = adapter.calls[0]

    assert 'senior data centre delivery planner' in call['system']
    assert 'You DO NOT invent' in call['system']
    assert 'DISCLOSE, DO NOT LAUNDER' in call['system']
    # The closed vocabulary really is in the prompt.
    assert 'frag.mep.power_train' in call['user']
    assert 'path.nm.peso_hsd' in call['user']
    # And the unverified marker is visible to the model, not hidden from it.
    assert 'UNVERIFIED MODEL-GENERATED PLACEHOLDER' in call['user']


def test_reason_stage_rejects_an_unknown_stage():
    with pytest.raises(ValueError, match='Unknown stage'):
        reason_stage('demolition', BRIEF, adapter=FakeAdapter(good_response()))


def test_every_canonical_stage_can_be_gathered():
    for stage in STAGES:
        libs = gather_stage_libraries(stage, 'Navi Mumbai')
        assert set(libs) == {'fragnets', 'gates', 'long_lead', 'decision_points'}


def test_empty_response_yields_no_selections_but_still_warns(libs, hits):
    result = run({'stage': STAGE, 'packages': [], 'gates': [], 'long_lead': [],
                  'decision_points': []}, libs, hits)
    assert result.packages == [] and result.trail == []
    assert any('Nothing here is auditable' in w for w in result.warnings)


# ------------------------------------------------------------------- procurement stage


def test_procurement_is_a_walk_stage_positioned_before_construction():
    """DOMAIN_KNOWLEDGE.md §2 orders Statutory (3) -> Procurement (4) -> Civil (6);
    SIMULATION_AND_REASONING.md §2 walks engineering -> procurement -> construction."""
    from backend.app.reasoning import PROCUREMENT_STAGE, STAGES

    assert PROCUREMENT_STAGE in STAGES
    i = STAGES.index('procurement')
    assert STAGES[i - 1] == 'approvals'
    assert i < STAGES.index('enabling') < STAGES.index('substructure')
    assert i < STAGES.index('mep_power')


def test_procurement_stage_has_a_department():
    from backend.app.reasoning import STAGE_DEPARTMENT
    assert STAGE_DEPARTMENT['procurement'] == 'procurement'


def test_procurement_owns_the_whole_long_lead_register():
    """It has no fragnets, so deriving the register from fragnets would leave it empty - at the
    one stage where long-lead exposure most needs reasoning about."""
    from backend.app.libraries import load_library

    libs = gather_stage_libraries('procurement', 'Navi Mumbai')
    assert libs['fragnets'] == [], 'procurement fragnets are future library data (frag.procurement.*)'

    all_leads = {e['id'] for e in load_library('equipment_lead_times')['entries']}
    assert {e['id'] for e in libs['long_lead']} == all_leads


def test_other_stages_still_derive_long_lead_from_their_fragnets():
    """The procurement special case must not leak into every stage."""
    libs = gather_stage_libraries('mep_power', 'Navi Mumbai')
    ids = {e['id'] for e in libs['long_lead']}
    assert 'lead.transformer_hv' in ids
    assert 'lead.chiller' not in ids, 'mep_power must not receive the whole register'


def test_the_long_lead_fork_now_fires_at_procurement_not_mep():
    """dp.long_lead_unconfirmed drives RFS; before the procurement stage it was not raised until
    mep_power, by which point construction was already being sequenced."""
    from backend.app.reasoning import STAGES

    fires_at = [
        s for s in STAGES
        if 'dp.long_lead_unconfirmed' in
        {d['id'] for d in gather_stage_libraries(s, 'Navi Mumbai')['decision_points']}
    ]
    assert fires_at == ['procurement']


def test_delivery_mode_still_fires_per_discipline():
    """Deliberate: DOMAIN_KNOWLEDGE.md §6 defines it as per-discipline, so procurement is an
    ADDITIONAL early firing point, not a replacement for the per-discipline ones."""
    from backend.app.reasoning import STAGES

    fires_at = [
        s for s in STAGES
        if 'dp.delivery_mode' in
        {d['id'] for d in gather_stage_libraries(s, 'Navi Mumbai')['decision_points']}
    ]
    assert 'procurement' in fires_at
    for discipline_stage in ('substructure', 'mep_power', 'fit_out'):
        assert discipline_stage in fires_at, f'{discipline_stage} lost its delivery-mode fork'
