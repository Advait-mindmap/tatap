"""Answering the delivery-mode fork has to change the plan.

Reported: a brief that never stated a mode for BMS produced BMS work assigned to a subcontract
package, even though the planner answered the delivery-mode fork. Tracing it found something
worse than a resource-assignment bug - `Simulator.brief` is set once at construction and no answer
ever writes back to it, so `dp.delivery_mode` stopped the run, asked the question, recorded the
reply and changed nothing. The same fork on an earlier run was answered "Subcontract" and was
equally discarded.

That breaks the rule the product is built on (CLAUDE.md rule 3): stop where thought breaks, ask,
and USE the answer. A fork whose answer goes nowhere is worse than no fork, because it buys the
planner's confidence without spending it on anything.

THE RULE FOR A BARE ANSWER. "Subcontract" answered to a question about every discipline is
ambiguous, and the resolution is the same principle the libraries use for unverified estimates: an
inferred answer never silently overwrites stated content. A bare mode fills only the disciplines
the brief left BLANK. A discipline the brief named keeps what the brief said, even when it sits
squarely inside the fork's stage scope.
"""

from __future__ import annotations

import pytest

from backend.app.llm_stub import StubAdapter
from backend.app.simulator import DecisionAnswer, Simulator

#: Deliberately silent about `bms` and `mechanical`, and explicit about `fire` and `civil`. The
#: gap is what a bare answer may fill; the statements are what it must not touch.
BRIEF = {
    'project_name': 'Delivery modes', 'city': 'Navi Mumbai', 'tier': 'III',
    'it_load_mw': 20.0, 'redundancy_topology': 'N+1', 'site_context': 'greenfield',
    'delivery_mode_by_discipline': {
        'civil': 'self-perform',
        'electrical': 'turnkey',
        'fire': 'subcontract',
    },
}

STATED = dict(BRIEF['delivery_mode_by_discipline'])


def walk_to(simulator: Simulator, decision_id: str, rounds: int = 40):
    """Run until `decision_id` is open, answering everything else with its first option."""
    for _ in range(rounds):
        list(simulator.run())
        if decision_id in simulator.state.pending_decisions:
            return True
        if not simulator.is_halted:
            return False
        for fork in sorted(simulator.state.pending_decisions):
            payload = simulator.state.pending_decisions[fork]
            options = payload.get('options') or ['Confirmed']
            simulator.answer(DecisionAnswer(decision_point_id=fork, answer=options[0]))
    return False


@pytest.fixture
def simulator():
    return Simulator(dict(BRIEF, delivery_mode_by_discipline=dict(STATED)),
                     run_id='dm-answers', adapter=StubAdapter())


# ------------------------------------------------------------------ the write-back

def test_a_bare_answer_fills_the_disciplines_the_brief_left_blank(simulator):
    """The reported failure. Before the fix the brief was untouched and BMS stayed unstated."""
    assert walk_to(simulator, 'dp.delivery_mode'), 'the run never raised the delivery-mode fork'
    assert 'bms' not in simulator.brief['delivery_mode_by_discipline']

    simulator.answer(DecisionAnswer(decision_point_id='dp.delivery_mode', answer='Subcontract'))

    modes = simulator.brief['delivery_mode_by_discipline']
    assert modes.get('bms') == 'subcontract', (
        'the fork was answered and BMS is still unstated - the answer went nowhere'
    )
    assert modes.get('mechanical') == 'subcontract'


def test_a_bare_answer_never_overrides_what_the_brief_stated(simulator):
    """The half that protects the brief.

    A stated discipline is verified content. An answer to a general question is an inference over
    the gaps, and the same principle that stops an unverified estimate speaking with the voice of
    a measurement stops it here.
    """
    assert walk_to(simulator, 'dp.delivery_mode')
    simulator.answer(DecisionAnswer(decision_point_id='dp.delivery_mode', answer='Self-perform'))

    modes = simulator.brief['delivery_mode_by_discipline']
    for discipline, stated in STATED.items():
        assert modes[discipline] == stated, (
            f'{discipline} was stated {stated!r} in the brief and the fork overwrote it with '
            f'{modes[discipline]!r}'
        )
    # And it did fill the blanks, so this is not passing by doing nothing at all.
    assert modes.get('bms') == 'self-perform'


def test_the_filled_mode_reaches_the_activities(simulator):
    """A write-back nobody reads is the same bug one layer down."""
    assert walk_to(simulator, 'dp.delivery_mode')
    simulator.answer(DecisionAnswer(decision_point_id='dp.delivery_mode', answer='Self-perform'))
    for _ in range(40):
        list(simulator.run())
        if not simulator.is_halted:
            break
        for fork in sorted(simulator.state.pending_decisions):
            options = simulator.state.pending_decisions[fork].get('options') or ['Confirmed']
            simulator.answer(DecisionAnswer(decision_point_id=fork, answer=options[0]))

    output = simulator.output().model_dump()
    controls = [a for a in output['activities'] if a.get('discipline') == 'controls']
    assert controls, 'no BMS work in the plan'
    assert {a['delivery_mode'] for a in controls} == {'self-perform'}, (
        'the answered mode never reached the BMS activities'
    )
    # Fire was stated subcontract and must be unaffected, in the same fragnet.
    fire = [a for a in output['activities']
            if a.get('discipline') == 'fire' and a['stage'] == 'fire_bms']
    assert {a['delivery_mode'] for a in fire} <= {'subcontract'}


# ------------------------------------------------------------------ "Mixed"

def test_mixed_raises_a_follow_up_for_each_blank_discipline(simulator):
    """"Mixed - specify per discipline" has to collect the specification it promises.

    Recording the phrase and moving on is the same defect as dropping the answer: the option
    offers granularity and delivers none.
    """
    assert walk_to(simulator, 'dp.delivery_mode')
    simulator.answer(DecisionAnswer(
        decision_point_id='dp.delivery_mode', answer='Mixed - specify per discipline',
    ))

    followups = [d for d in simulator.state.pending_decisions if d.startswith('dyn.delivery_mode.')]
    assert followups, 'Mixed collected no specification at all'

    # One per BLANK discipline - never for one the brief already stated.
    asked = {d.rsplit('.', 1)[-1] for d in followups}
    assert 'bms' in asked
    assert not (asked & set(STATED)), (
        f'Mixed is asking about disciplines the brief already stated: {asked & set(STATED)}'
    )
    for fork in followups:
        payload = simulator.state.pending_decisions[fork]
        assert payload['options'], f'{fork} offers nothing to choose'
        assert payload.get('blocking') is True


def test_answering_a_mixed_follow_up_sets_that_discipline_only(simulator):
    assert walk_to(simulator, 'dp.delivery_mode')
    simulator.answer(DecisionAnswer(
        decision_point_id='dp.delivery_mode', answer='Mixed - specify per discipline',
    ))
    before = dict(simulator.brief['delivery_mode_by_discipline'])

    simulator.answer(DecisionAnswer(
        decision_point_id='dyn.delivery_mode.bms', answer='Self-perform',
    ))
    modes = simulator.brief['delivery_mode_by_discipline']

    assert modes['bms'] == 'self-perform'
    for discipline, value in before.items():
        assert modes[discipline] == value, f'{discipline} changed while answering about bms'


def test_mixed_with_nothing_blank_asks_nothing():
    """Every discipline stated means there is nothing to specify, and inventing a question the
    planner cannot usefully answer is its own kind of noise."""
    full = {d: 'self-perform' for d in
            ('civil', 'structure', 'electrical', 'mechanical', 'gensets', 'fire', 'bms')}
    simulator = Simulator(dict(BRIEF, delivery_mode_by_discipline=full),
                          run_id='dm-full', adapter=StubAdapter())
    assert walk_to(simulator, 'dp.delivery_mode')
    simulator.answer(DecisionAnswer(
        decision_point_id='dp.delivery_mode', answer='Mixed - specify per discipline',
    ))
    followups = [d for d in simulator.state.pending_decisions if d.startswith('dyn.delivery_mode.')]
    assert not followups, f'asked about disciplines that are all stated: {followups}'


def test_an_unrecognised_answer_changes_nothing(simulator):
    """Free text that names no mode must not be guessed into one."""
    assert walk_to(simulator, 'dp.delivery_mode')
    before = dict(simulator.brief['delivery_mode_by_discipline'])
    simulator.answer(DecisionAnswer(
        decision_point_id='dp.delivery_mode', answer='we are still deciding',
    ))
    assert simulator.brief['delivery_mode_by_discipline'] == before
