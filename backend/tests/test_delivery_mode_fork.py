"""A discipline the brief never mentions must be ASKED about, not inherited silently.

Verified on a real production run (run-4b8db1e64b844916a010f2b7d979059c): all 46 BMS activities
were assigned to "Controls and BMS subcontract package", and the delivery-mode fork never
appeared at all. The write-back that was supposed to fix this was correct and unreachable.

Tracing found two mechanisms that look like one:

  * THE FORK IS ELECTIVE. `dp.delivery_mode` is tagged onto stages via `applies_to_stages`, and
    fire_bms does carry the tags - but tagging only puts it in the candidate list handed to the
    reasoner. It is raised only if the MODEL names it in its response. With a real LLM that
    varies run to run, which is why it fired at fire_bms once and never again.

  * THE FALLBACK IS UNCONDITIONAL. `_delivery_mode_for` resolves a discipline the brief left
    blank to the mode of its STAGE's discipline - controls inherits fire - with no fork, no
    confidence test and no record that anything was assumed.

So a genuine fork silently resolved to a default, which is the one thing CLAUDE.md rule 3 exists
to prevent. Stopping where thought breaks cannot be at the model's discretion.

The fix makes the trigger deterministic and keys it to the same condition the write-back writes
on: a discipline with work in this stage and no stated mode in the brief raises
`dyn.delivery_mode.<discipline>`.
"""

from __future__ import annotations

import pytest

from backend.app.simulator import DecisionAnswer, Simulator
from backend.tests.test_silent_fork_defaults import SilentAdapter

#: States fire and civil, says NOTHING about bms. The gap is what must be asked about; the
#: statements are what must never be asked about.
BRIEF = {
    'project_name': 'Delivery mode forks', 'city': 'Navi Mumbai', 'tier': 'III',
    'it_load_mw': 20.0, 'redundancy_topology': 'N+1', 'site_context': 'greenfield',
    'delivery_mode_by_discipline': {'fire': 'subcontract', 'civil': 'self-perform'},
}

BMS_FORK = 'dyn.delivery_mode.bms'


def fresh(brief=None):
    """A simulator whose reasoner raises NO curated forks.

    The adapter is explicit for two reasons. It is the production condition - the real reasoner
    declined to raise `dp.delivery_mode`, which is the whole bug - so a fork appearing here can
    only have come from the engine. And without it the simulator resolves the default provider,
    which `.env` sets to base44: these tests then drive the live gateway, spend credits, and take
    minutes per case instead of seconds. That happened once, on this file.
    """
    payload = dict(BRIEF)
    payload['delivery_mode_by_discipline'] = dict(
        (brief or BRIEF)['delivery_mode_by_discipline']
    )
    return Simulator(payload, adapter=SilentAdapter())


def walk(simulator, *, answer_bms=None, rounds=40):
    """Run to completion, answering every fork with its first option.

    `answer_bms` overrides the answer given to the BMS delivery-mode fork, so one helper covers
    both "was it asked" and "did answering it change the plan".
    """
    seen = set()
    for _ in range(rounds):
        list(simulator.run())
        seen |= set(simulator.state.pending_decisions)
        if not simulator.is_halted:
            break
        for fork in sorted(simulator.state.pending_decisions):
            payload = simulator.state.pending_decisions[fork]
            options = payload.get('options') or ['Confirmed']
            reply = answer_bms if (fork == BMS_FORK and answer_bms) else options[0]
            simulator.answer(DecisionAnswer(decision_point_id=fork, answer=reply))
    return seen


def bms_activities(simulator):
    return [
        a for a in simulator.output().activities
        if 'controls' in str(a.get('discipline', '')).lower()
        or 'bms' in str(a.get('discipline', '')).lower()
    ]


def test_a_discipline_the_brief_never_states_is_asked_about():
    """The whole point. Silence in the brief is a question, not a licence to inherit."""
    simulator = fresh()
    seen = walk(simulator)
    assert BMS_FORK in seen, (
        'BMS work was planned without ever asking how it is delivered; it inherited the fire '
        f'scope silently. Forks raised: {sorted(f for f in seen if "delivery_mode" in f)}'
    )


def test_a_discipline_the_brief_states_is_never_asked_about():
    """A stated mode is verified content. Asking about it again is noise, and answering it
    could overwrite the brief - the exact failure the write-back rule forbids."""
    simulator = fresh()
    seen = walk(simulator)
    assert 'dyn.delivery_mode.fire' not in seen
    assert 'dyn.delivery_mode.civil' not in seen


def test_answering_the_fork_changes_how_bms_is_planned():
    """A fork whose answer changes nothing is theatre (CLAUDE.md rule 3)."""
    inherited = fresh()
    walk(inherited, answer_bms='Subcontract')
    subcontracted = {a['id'] for a in bms_activities(inherited)}

    chosen = fresh()
    walk(chosen, answer_bms='Self-perform')
    modes = chosen.brief.get('delivery_mode_by_discipline') or {}

    assert modes.get('bms') == 'self-perform', (
        f'answering the fork did not reach the brief: {modes}'
    )
    assert subcontracted, 'no BMS activities were planned at all; the test proves nothing'


def test_the_fork_is_not_re_asked_once_answered():
    """The brief is the memo. Once a mode is written back, later stages must not ask again."""
    simulator = fresh()
    asked = 0
    for _ in range(40):
        list(simulator.run())
        if BMS_FORK in simulator.state.pending_decisions:
            asked += 1
        if not simulator.is_halted:
            break
        for fork in sorted(simulator.state.pending_decisions):
            payload = simulator.state.pending_decisions[fork]
            simulator.answer(DecisionAnswer(
                decision_point_id=fork,
                answer=(payload.get('options') or ['Confirmed'])[0],
            ))
    assert asked <= 1, f'the BMS fork was raised {asked} times'
