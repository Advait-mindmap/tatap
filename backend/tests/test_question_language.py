"""Questions a project engineer can answer without knowing our internals.

Reported from real use: forks reach the screen reading

    "Does frag.fire_bms.detection_suppression apply to the fire_bms stage on this project?"
    "The reasoner stated confidence 0.55, below the 0.70 threshold."

Both are internal telemetry. A civil engineer does not know what a fragnet id is, and "stated
0.55" is our model reporting on itself, not a fact about their project. The question underneath is
a real one - is this scope on this job - and it is unanswerable in that form.

The translation layer already existed and was applied to ONE path. `readable.humanise` rewrites
identifiers into library names and has been cleaning the reasoning trail since it was built; the
questions never went through it. A missed boundary, not a missing capability.

TECHNICAL IDENTIFIERS ARE KEPT, not dropped - same rule the trail already follows. Prose reads as
engineering language, the codes stay beside it for anyone tracing a fork back to the library entry
that raised it.

These tests build the low-confidence fork DIRECTLY rather than hoping a stub run produces one. The
stub reports high confidence on everything, so a run-level test passes without the reported text
ever existing - which is how this reached a user in the first place.
"""

from __future__ import annotations

import re

import pytest

from backend.app.reasoning.loop import build_stage_reasoning, gather_stage_libraries

STAGE = 'mep_power'

#: Shapes that mean something to us and nothing on site.
INTERNAL_SHAPES = ('frag.', 'gate.', 'lead.', 'safety.', 'path.', 'dyn.', 'dp.', 'stat.')
DECIMAL = re.compile(r'\b\d\.\d+\b')


@pytest.fixture(scope='module')
def libs():
    return gather_stage_libraries(STAGE, 'Navi Mumbai')


def response_with_low_confidence(*, confidence=0.55):
    return {
        'stage': STAGE,
        'packages': [{
            'fragnet_id': 'frag.mep.power_train',
            'why': 'The power train is the core MEP scope for this stage.',
            'confidence': confidence,
            'sources': ['corpus:1#0'],
            'predecessors': [],
        }],
        'gates': [], 'long_lead': [], 'decision_points': [], 'notes': '',
    }


def hits():
    return [{
        'ref': 'corpus:1#0', 'doc_id': 1, 'chunk_id': 1, 'kind': 'real_execution',
        'score': 0.8, 'verified': False, 'citable_as_precedent': True,
        'text': 'On the delivered project the transformer arrived late.', 'title': 'Project X',
    }]


def low_confidence_fork(libs, **kwargs):
    result = build_stage_reasoning(
        response_with_low_confidence(**kwargs), stage=STAGE, libs=libs, hits=hits(),
        threshold=0.7, grounded=True,
    )
    dyn = [d for d in result.decision_points if d.detection == 'dynamic']
    assert dyn, 'no low-confidence fork was raised, so there is nothing to read'
    return dyn[0]


def test_the_question_does_not_show_an_internal_identifier(libs):
    """The headline. A planner reads the question; it must be in their language."""
    fork = low_confidence_fork(libs)
    found = [shape for shape in INTERNAL_SHAPES if shape in fork.question]
    assert not found, f'the question carries {found}: {fork.question!r}'


def test_the_explanation_does_not_show_an_internal_identifier(libs):
    fork = low_confidence_fork(libs)
    found = [shape for shape in INTERNAL_SHAPES if shape in fork.why_stuck]
    assert not found, f'the explanation carries {found}: {fork.why_stuck!r}'


def test_no_confidence_score_reaches_the_reader(libs):
    """"stated 0.55, below the 0.70 threshold" is telemetry. A site engineer cannot act on it."""
    fork = low_confidence_fork(libs)
    assert not DECIMAL.search(fork.question), fork.question
    assert not DECIMAL.search(fork.why_stuck), fork.why_stuck


def test_it_still_says_why_it_is_asking(libs):
    """Removing the number must not remove the reason. The plan is resting on an estimate, and
    that is precisely what the reader is being asked to confirm."""
    fork = low_confidence_fork(libs)
    text = f'{fork.question} {fork.why_stuck}'.lower()
    assert any(
        phrase in text
        for phrase in ('not confirmed', 'best estimate', 'not yet verified', 'unconfirmed',
                       'estimate')
    ), f'the fork gives no plain reason for asking: {fork.why_stuck!r}'


def test_the_work_is_named_the_way_a_planner_would_name_it(libs):
    """`frag.mep.power_train` should read as the work it points at. The library already carries
    that name - nothing here invents one."""
    from backend.app.libraries import load_library

    name = next(
        e['name'] for e in load_library('fragnets')['entries'] if e['id'] == 'frag.mep.power_train'
    )
    fork = low_confidence_fork(libs)
    assert name.lower() in f'{fork.question} {fork.why_stuck}'.lower(), (
        f'the identifier was removed but not translated: {fork.question!r}'
    )


def test_the_identifier_is_kept_for_audit(libs):
    """Kept, not dropped. A reviewer tracing this fork back to the library entry needs the code;
    it just does not belong in the sentence."""
    fork = low_confidence_fork(libs)
    assert 'frag.mep.power_train' in (fork.technical_refs or []), (
        f'the audit trail was lost rather than moved: {fork.technical_refs}'
    )


def test_the_batched_fork_reads_as_plainly_as_the_single_one(libs):
    """Several low-confidence items are asked as one question. That path builds its own sentence
    and would otherwise keep listing raw ids."""
    response = response_with_low_confidence()
    response['gates'] = [{
        'gate_id': 'path.nm.peso_hsd',
        'why': 'Bulk diesel storage for the generators requires PESO licensing.',
        'confidence': 0.4, 'sources': ['corpus:1#0'],
    }]
    result = build_stage_reasoning(
        response, stage=STAGE, libs=libs, hits=hits(), threshold=0.7, grounded=True,
    )
    dyn = [d for d in result.decision_points if d.detection == 'dynamic']
    assert dyn, 'no batched fork was raised'
    for fork in dyn:
        found = [shape for shape in INTERNAL_SHAPES if shape in f'{fork.question} {fork.why_stuck}']
        assert not found, f'{fork.decision_point_id} carries {found}: {fork.why_stuck!r}'
        assert not DECIMAL.search(fork.why_stuck), fork.why_stuck


def test_the_question_still_asks_something(libs):
    """A translation that empties the question is not an improvement."""
    fork = low_confidence_fork(libs)
    assert len(fork.question.strip()) > 20
    assert fork.question.strip() != fork.decision_point_id
