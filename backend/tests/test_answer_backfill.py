"""An answer that was recorded but never applied must still count.

Found on a real run. `dp.greenfield_brownfield` was answered "Brownfield - inside a live hall",
the answer is in the run's stored decisions to this day, and the brief's `site_context` is None -
because the write-back that sets it shipped AFTER that run was answered. The delivery-mode
write-back had shipped earlier, so `delivery_mode_by_discipline` on the same run is fully
populated. One answer applied, one dropped, on the same run, for no reason a reader could see.

Re-assembly cannot rescue it, because re-assembly faithfully re-reads a brief that never got the
value. The plan therefore reports no site context, the live-hall rule matches nothing, and the
export gate that exists to catch a missing live-site control compares the same absent value and
reports nothing missing.

THE ANSWERS ARE THE RECORD; the brief fields are a derived convenience. So the write-backs are
replayed from the stored answers whenever the brief is silent about what an answer settles. That
repairs runs answered before a write-back existed, and it removes the whole class: a write-back
added tomorrow applies to every run that already answered its question.

Replay is safe to run repeatedly because every write-back only fills blanks - a discipline the
brief states keeps what the brief said, and a site context already set is left alone.
"""

from __future__ import annotations

import pytest

from backend.app.llm_stub import StubAdapter
from backend.app.simulator import DecisionAnswer, Simulator
from backend.app.simulator.events import RunState


def answered_run(**answers):
    """A run whose ANSWERS are recorded but whose brief never received them.

    This is the shape of the real run: `state.answers` holds the reply, the brief does not carry
    what the reply settles.
    """
    brief = {
        'project_name': 'Backfill', 'city': 'Navi Mumbai', 'tier': 'III',
        'it_load_mw': 20.0, 'redundancy_topology': 'N+1',
    }
    state = RunState(run_id='backfill', brief=dict(brief))
    for decision_id, text in answers.items():
        state.answers[decision_id] = {
            'answer': text, 'answered_by': 'planner', 'note': '',
            'stage': '', 'question': '', 'impact': '',
        }
    return Simulator(dict(brief), run_id='backfill', adapter=StubAdapter(), state=state)


def test_a_recorded_site_context_answer_reaches_the_brief():
    simulator = answered_run(**{'dp.greenfield_brownfield': 'Brownfield - inside a live hall'})
    simulator.output()
    assert simulator.brief.get('site_context') == 'brownfield', (
        f'the answer stayed a record and never became a fact: {simulator.brief.get("site_context")!r}'
    )


def test_the_replayed_answer_actually_governs_the_plan():
    """The point of it. Not "the field is set" - the tier-1 control that field gates now attaches.

    Reproduces the real run's exact shape: walk a run answering every fork, then REMOVE the
    site_context the write-back set. What is left is a completed run whose answers record a
    brownfield site and whose brief does not - which is precisely what a run answered before the
    write-back existed looks like on disk.
    """
    simulator = Simulator(
        {
            'project_name': 'Backfill', 'city': 'Navi Mumbai', 'tier': 'III',
            'it_load_mw': 20.0, 'redundancy_topology': 'N+1',
        },
        run_id='governs', adapter=StubAdapter(),
    )
    for _ in range(50):
        list(simulator.run())
        if not simulator.is_halted:
            break
        for fork in sorted(simulator.state.pending_decisions):
            pending = simulator.state.pending_decisions[fork]
            reply = ('Brownfield - adjacent to live halls'
                     if fork == 'dp.greenfield_brownfield'
                     else (pending.get('options') or ['Confirmed'])[0])
            simulator.answer(DecisionAnswer(decision_point_id=fork, answer=reply))

    assert 'dp.greenfield_brownfield' in simulator.state.answers, (
        'this run never answered the site-context fork, so it cannot stand in for the real one'
    )
    simulator.brief.pop('site_context', None)  # the old build: recorded, never applied

    output = simulator.output()
    floor = [
        a for a in output.activities
        if 'Raised floor' in str(a.get('name')) and a.get('type') == 'task'
    ]
    assert floor, 'no raised-floor work in this plan, so the test proves nothing'
    assert all(a.get('hitl_tier') == 'tier_1' for a in floor), (
        'the answer was replayed but the live-hall control still did not attach'
    )
    assert 'live_hall' in output.quality['export_block_reason']


def test_a_recorded_delivery_mode_answer_is_replayed_too():
    simulator = answered_run(**{'dp.delivery_mode': 'Subcontract'})
    simulator.output()
    modes = simulator.brief.get('delivery_mode_by_discipline') or {}
    assert modes.get('bms') == 'subcontract', f'delivery answer not replayed: {modes}'


def test_replay_never_overwrites_what_the_brief_states():
    """The rule the whole write-back design rests on, now that replay can happen at any time."""
    brief = {
        'project_name': 'Backfill', 'city': 'Navi Mumbai', 'tier': 'III',
        'it_load_mw': 20.0, 'redundancy_topology': 'N+1',
        'site_context': 'greenfield',
        'delivery_mode_by_discipline': {'bms': 'turnkey'},
    }
    state = RunState(run_id='stated', brief=dict(brief))
    state.answers['dp.greenfield_brownfield'] = {
        'answer': 'Brownfield - inside a live hall', 'answered_by': 'planner', 'note': '',
        'stage': '', 'question': '', 'impact': '',
    }
    state.answers['dp.delivery_mode'] = {
        'answer': 'Subcontract', 'answered_by': 'planner', 'note': '',
        'stage': '', 'question': '', 'impact': '',
    }
    simulator = Simulator(dict(brief), run_id='stated', adapter=StubAdapter(), state=state)
    simulator.output()

    assert simulator.brief['site_context'] == 'greenfield', 'replay overwrote a stated context'
    assert simulator.brief['delivery_mode_by_discipline']['bms'] == 'turnkey', (
        'replay overwrote a stated delivery mode'
    )


def test_replaying_twice_changes_nothing():
    simulator = answered_run(**{'dp.greenfield_brownfield': 'Brownfield - inside a live hall'})
    simulator.output()
    first = dict(simulator.brief)
    simulator.output()
    assert dict(simulator.brief) == first
