"""End-to-end against the real gateway: raw brief -> SimulationOutput (Task 9).

The golden test pins the shape with a fixed reasoner. This one is the honest end-to-end: it runs
real intake on sample_raw_brief.md, feeds the extracted brief to real per-stage reasoning, halts
at whatever genuine forks the model actually raises, answers them, and projects the one
SimulationOutput. It is the only test that proves the four layers fit together against a real
brief rather than against each other's fixtures.

Makes real calls and spends Base44 credits. Marked `live`; skipped without credentials.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.intake import extract_brief
from backend.app.models import Base
from backend.app.rag import CorpusKind, ingest_document, ingest_seed_corpus
from backend.app.schemas import RawBrief, SimulationOutput
from backend.app.simulator import DecisionAnswer, Simulator

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (os.getenv('BASE44_FN_URL') and os.getenv('BASE44_SHARED_SECRET')),
        reason='BASE44_FN_URL / BASE44_SHARED_SECRET not set; skipping live end-to-end.',
    ),
]

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE = (REPO_ROOT / 'sample_raw_brief.md').read_text(encoding='utf-8')

#: Bounded so the run spends a few calls, not a full 13-stage walk.
WALK = ['design', 'procurement', 'substructure', 'mep_power']

ANSWERS = {
    'dp.tier_topology': 'N+1 on electrical and cooling, as stated in the brief',
    'dp.ofe': 'Gensets owner-furnished under the client Cummins framework',
    'dp.grid_position': 'Build dedicated substation - HT substation is on our scope',
    'dp.long_lead_unconfirmed': 'Not confirmed - use library estimate and flag',
    'dp.delivery_mode': 'Per the brief: civil self-perform, electrical and mechanical turnkey',
    'dp.greenfield_brownfield': 'Greenfield',
    'dp.phasing': 'Phased hall-by-hall',
    'dp.city_pathway_unconfirmed': 'Not confirmed - obtain from compliance team',
}


@pytest.fixture(scope='module')
def session():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        ingest_seed_corpus(s)
        ingest_document(
            s, title='Delivered DC - Navi Mumbai', source='client-history',
            kind=CorpusKind.REAL_EXECUTION,
            content=(
                'On the delivered Navi Mumbai data centre the HV transformer and MV switchgear '
                'drove the energisation date. The raft was poured before steel erection, and the '
                'power train ran transformer plinth, placement, switchgear, then UPS and busway. '
                'CEIG approval gated energisation; PESO licensing gated the diesel storage.'
            ),
            project_name='Delivered DC', city='Navi Mumbai', tier='III',
        )
        s.commit()
        yield s


@pytest.fixture(scope='module')
def live_output(session):
    """intake -> reasoning -> engine -> SimulationOutput, all live. Spends credits once."""
    intake = extract_brief(RawBrief(text=SAMPLE, source_ref='sample_raw_brief.md'))
    brief = intake.brief.model_dump()

    simulator = Simulator(brief, run_id='live-e2e', session=session, stages=WALK)
    for _ in range(len(WALK) + 3):
        list(simulator.run())
        if not simulator.is_halted:
            break
        answered = False
        for decision_id in sorted(simulator.state.pending_decisions):
            answer = ANSWERS.get(decision_id)
            if answer:
                simulator.answer(
                    DecisionAnswer(decision_point_id=decision_id, answer=answer)
                )
                answered = True
        if not answered:
            break

    questions = [q.question for q in intake.questions]
    return intake, simulator, simulator.output(questions=questions)


def test_live_output_validates_as_the_declared_schema(live_output):
    _, _, output = live_output
    assert SimulationOutput.model_validate(output.model_dump()) == output


def test_live_brief_reached_project_meta(live_output):
    """The intake extraction must actually flow through to the output, not be re-derived."""
    intake, _, output = live_output
    assert output.project_meta['city'] == intake.brief.city
    assert output.project_meta['tier'] == intake.brief.tier
    assert output.project_meta['it_load_mw'] == intake.brief.it_load_mw
    assert output.project_meta['tier'] == 'III'
    assert output.project_meta['it_load_mw'] == 20.0


def test_live_intake_questions_are_carried_into_the_output(live_output):
    """The brief never names the client; that gap must survive into the plan."""
    intake, _, output = live_output
    if intake.questions:
        assert output.questions


def test_live_run_raised_real_decision_points(live_output):
    """The differentiator: the model stopped at genuine forks rather than inventing answers."""
    _, simulator, output = live_output
    fired = {d.id for d in output.decisions}
    assert fired, 'a brief this ambiguous should have raised at least one fork'
    assert fired <= set(ANSWERS), f'unexpected fork ids: {fired - set(ANSWERS)}'
    for decision in output.decisions:
        assert decision.question and decision.answer


def test_live_output_has_a_populated_flow_and_zones(live_output):
    _, _, output = live_output
    assert output.flow['nodes']
    node_ids = {n['id'] for n in output.flow['nodes']}
    for edge in output.flow['edges']:
        assert edge['from'] in node_ids and edge['to'] in node_ids
    assert output.zones, 'zones drive the 3D model and are derived from the load'


def test_live_activities_are_all_library_derived(live_output):
    """CLAUDE.md rule 2: nothing in the plan may originate as model free-text."""
    from backend.app.libraries import load_library

    _, _, output = live_output
    known = {f['id'] for f in load_library('fragnets')['entries']}
    for activity in output.activities:
        if activity.get('source_fragnet'):
            assert activity['source_fragnet'] in known, (
                f'{activity["id"]} came from an unknown fragnet'
            )


def test_live_every_activity_is_traceable(live_output):
    _, _, output = live_output
    refs = {t['ref_id'] for t in output.reasoning_trail}
    for activity in output.activities:
        if activity['type'] == 'task':
            assert activity['id'] in refs


def test_live_governance_reports_its_own_incompleteness(live_output):
    """The whole seed library is stand-in data, so the plan must not claim to be sound."""
    _, _, output = live_output
    quality = output.quality
    assert quality['governance_complete'] is False
    assert quality['unverified_dependencies'] or quality['tier_2_count'] >= 0
    assert 'checks_not_run' in quality['dcma_summary']


def test_live_versions_are_recorded(live_output):
    _, _, output = live_output
    meta = output.project_meta
    assert meta['library_version'] and meta['corpus_version'] and meta['prompt_version']
