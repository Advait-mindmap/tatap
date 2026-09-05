"""The simulator: graph walk, event stream, stop-and-ask (Task 8).

The behaviour these tests exist to hold:

1. The walk runs stages in execution order and emits the event vocabulary the spec names.
2. **Stop-and-ask actually stops.** A blocking fork halts the walk *before* anything downstream
   is assembled, nothing is invented to get past it, and the answer resumes from where it
   stopped rather than restarting.
3. A halted run survives its socket, so an answer arriving later still resumes it.

No live gateway: `reason_stage` is substituted so the walk can be driven deterministically.
"""

from __future__ import annotations

from typing import Dict, List

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app.main import app
from backend.app.reasoning.stages import STAGES
from backend.app.schemas import (
    GateSelection,
    PackageSelection,
    RaisedDecisionPoint,
    StageReasoning,
)
from backend.app.simulator import (
    ACTIVITY_ADDED,
    DECISION_NEEDED,
    DECISION_RESOLVED,
    GATE_INSERTED,
    PACKAGE_EXPANDED,
    SIMULATION_COMPLETED,
    SIMULATION_ERROR,
    SIMULATION_HALTED,
    SIMULATION_STARTED,
    SPEC_EVENT_TYPES,
    STAGE_COMPLETED,
    STAGE_STARTED,
    DecisionAnswer,
    RunState,
    Simulator,
    registry,
    run_to_completion,
)

BRIEF = {
    'project_name': 'POC DC', 'city': 'Navi Mumbai', 'tier': 'III', 'it_load_mw': 20.0,
    'redundancy_topology': 'N+1',
    'delivery_mode_by_discipline': {'civil': 'self-perform', 'electrical': 'turnkey'},
}

WALK = ['design', 'procurement', 'substructure', 'mep_power', 'commissioning']

STAGE_FRAGNETS = {
    'substructure': 'frag.substructure.raft',
    'mep_power': 'frag.mep.power_train',
    'commissioning': 'frag.commissioning.ladder',
}


def pkg(fragnet_id):
    return PackageSelection(
        fragnet_id=fragnet_id, why='applies', confidence=0.9, effective_confidence=0.5,
        sources=['corpus:1#0'], unverified_dependencies=[fragnet_id],
    )


def fork(decision_id, blocking=True):
    return RaisedDecisionPoint(
        decision_point_id=decision_id,
        question=f'Answer {decision_id}?',
        why_stuck='The flow of thought cannot continue without this.',
        options=['A', 'B'], impact='Changes the plan', blocking=blocking, detection='curated',
    )


class FakeReasoner:
    """Stands in for reason_stage. Records what decisions it was told about."""

    def __init__(self, forks_by_stage: Dict[str, List[RaisedDecisionPoint]] | None = None,
                 gates_by_stage: Dict[str, List[str]] | None = None, raises_at: str = ''):
        self.forks_by_stage = forks_by_stage or {}
        self.gates_by_stage = gates_by_stage or {}
        self.raises_at = raises_at
        self.calls: List[tuple] = []

    def __call__(self, stage, brief, *, session=None, decisions=None, adapter=None, **kw):
        self.calls.append((stage, tuple(d['id'] for d in (decisions or []))))
        if stage == self.raises_at:
            raise RuntimeError('gateway exploded')
        fragnet = STAGE_FRAGNETS.get(stage)
        return StageReasoning(
            stage=stage,
            packages=[pkg(fragnet)] if fragnet else [],
            gates=[
                GateSelection(gate_id=g, why='gate', confidence=0.8, effective_confidence=0.5)
                for g in self.gates_by_stage.get(stage, [])
            ],
            decision_points=self.forks_by_stage.get(stage, []),
            library_version='v1', corpus_version='v1', prompt_version='v1',
        )


@pytest.fixture(autouse=True)
def clean_registry():
    registry.clear()
    yield
    registry.clear()


def make(monkeypatch, reasoner, **kwargs):
    monkeypatch.setattr('backend.app.simulator.runner.reason_stage', reasoner)
    return Simulator(BRIEF, stages=WALK, **kwargs)


def types_of(events):
    return [e.type for e in events]


# ------------------------------------------------------------------------- the walk


def test_walk_runs_stages_in_execution_order(monkeypatch):
    reasoner = FakeReasoner()
    events = list(make(monkeypatch, reasoner).run())

    started = [e.stage for e in events if e.type == STAGE_STARTED]
    assert started == WALK
    assert [c[0] for c in reasoner.calls] == WALK


def test_walk_follows_the_canonical_stage_order_by_default(monkeypatch):
    monkeypatch.setattr('backend.app.simulator.runner.reason_stage', FakeReasoner())
    assert Simulator(BRIEF).stages == list(STAGES)
    assert Simulator(BRIEF).stages[0] == 'design'


def test_emits_every_event_type_the_spec_names(monkeypatch):
    """SIMULATION_AND_REASONING.md §2 names eight; the 2D/3D views depend on each."""
    reasoner = FakeReasoner(
        forks_by_stage={'procurement': [fork('dp.long_lead_unconfirmed')]},
        gates_by_stage={'mep_power': ['path.nm.peso_hsd']},
    )
    simulator = make(monkeypatch, reasoner)
    events = run_to_completion(simulator, {'dp.long_lead_unconfirmed': 'Confirmed by PO'})

    emitted = set(types_of(events))
    assert set(SPEC_EVENT_TYPES) <= emitted, f'missing: {set(SPEC_EVENT_TYPES) - emitted}'


def test_events_are_sequentially_numbered(monkeypatch):
    events = list(make(monkeypatch, FakeReasoner()).run())
    assert [e.seq for e in events] == list(range(1, len(events) + 1))


def test_activity_added_carries_what_the_views_need(monkeypatch):
    """The 2D flow draws from these payloads, so the governance must be on the wire."""
    events = list(make(monkeypatch, FakeReasoner()).run())
    added = [e for e in events if e.type == ACTIVITY_ADDED]
    assert added

    payload = added[0].payload
    for key in ('id', 'name', 'wbs_id', 'duration_days', 'dept_code', 'predecessors',
                'hitl_tier', 'safety_flag', 'confidence', 'unverified_dependencies',
                'trail_ref'):
        assert key in payload, f'activity_added payload missing {key}'


def test_activities_are_emitted_once_each(monkeypatch):
    """Re-assembly per stage must not re-emit activities already drawn."""
    events = list(make(monkeypatch, FakeReasoner()).run())
    ids = [e.payload['id'] for e in events if e.type == ACTIVITY_ADDED]
    assert len(ids) == len(set(ids))


def test_package_expanded_precedes_its_activities(monkeypatch):
    events = list(make(monkeypatch, FakeReasoner()).run())
    first_package = next(i for i, e in enumerate(events) if e.type == PACKAGE_EXPANDED)
    first_activity = next(i for i, e in enumerate(events) if e.type == ACTIVITY_ADDED)
    assert first_package < first_activity


def test_completion_reports_governance_and_export_block(monkeypatch):
    events = list(make(monkeypatch, FakeReasoner()).run())
    done = next(e for e in events if e.type == SIMULATION_COMPLETED)

    assert done.payload['stages_completed'] == WALK
    assert done.payload['activity_count'] > 0
    # The commissioning ladder carries Tier-1 IST, so export must be blocked.
    assert done.payload['export_blocked'] is True
    assert done.payload['governance']['tier_1_count'] > 0


# ------------------------------------------------------------------- stop-and-ask


def test_a_blocking_fork_halts_the_walk(monkeypatch):
    reasoner = FakeReasoner(forks_by_stage={'procurement': [fork('dp.long_lead_unconfirmed')]})
    simulator = make(monkeypatch, reasoner)
    events = list(simulator.run())

    assert types_of(events)[-1] == SIMULATION_HALTED
    assert simulator.is_halted is True
    assert simulator.state.halted_at == 'procurement'
    # Stages after the fork were never reasoned about.
    assert [c[0] for c in reasoner.calls] == ['design', 'procurement']


def test_nothing_downstream_is_assembled_past_a_fork(monkeypatch):
    """CLAUDE.md rule 3: it never guesses past a genuine fork."""
    reasoner = FakeReasoner(forks_by_stage={'procurement': [fork('dp.ofe')]})
    simulator = make(monkeypatch, reasoner)
    events = list(simulator.run())

    stages_with_activities = {e.stage for e in events if e.type == ACTIVITY_ADDED}
    assert 'substructure' not in stages_with_activities
    assert 'mep_power' not in stages_with_activities
    assert simulator.state.completed_stages == ['design']


def test_decision_needed_carries_the_spec_payload(monkeypatch):
    """§4: {id, question, why_stuck, options[], impact, blocking}."""
    reasoner = FakeReasoner(forks_by_stage={'procurement': [fork('dp.grid_position')]})
    events = list(make(monkeypatch, reasoner).run())
    needed = next(e for e in events if e.type == DECISION_NEEDED)

    assert set(needed.payload) >= {'id', 'question', 'why_stuck', 'options', 'impact', 'blocking'}
    assert needed.payload['id'] == 'dp.grid_position'
    assert needed.payload['blocking'] is True
    assert needed.payload['why_stuck']


def test_answering_resumes_from_where_it_halted(monkeypatch):
    reasoner = FakeReasoner(forks_by_stage={'procurement': [fork('dp.ofe')]})
    simulator = make(monkeypatch, reasoner)
    list(simulator.run())

    simulator.answer(DecisionAnswer(decision_point_id='dp.ofe', answer='Owner-furnished'))
    resumed = list(simulator.run())

    assert types_of(resumed)[0] == DECISION_RESOLVED
    assert types_of(resumed)[-1] == SIMULATION_COMPLETED
    assert simulator.is_halted is False
    # design was completed before the halt and is not walked again.
    assert [c[0] for c in reasoner.calls] == [
        'design', 'procurement', 'procurement', 'substructure', 'mep_power', 'commissioning'
    ]


def test_the_answer_reaches_the_reasoner_on_resume(monkeypatch):
    """Downstream reasoning must see the decision it depends on (§4)."""
    reasoner = FakeReasoner(forks_by_stage={'procurement': [fork('dp.ofe')]})
    simulator = make(monkeypatch, reasoner)
    list(simulator.run())
    simulator.answer(DecisionAnswer(decision_point_id='dp.ofe', answer='Owner-furnished'))
    list(simulator.run())

    later = [decisions for stage, decisions in reasoner.calls if stage == 'mep_power']
    assert later and 'dp.ofe' in later[0]


def test_resolved_fork_is_not_raised_again(monkeypatch):
    """The reasoner may re-raise a fork it was already told about; the simulator must not stop."""
    reasoner = FakeReasoner(forks_by_stage={'procurement': [fork('dp.ofe')]})
    simulator = make(monkeypatch, reasoner)
    list(simulator.run())
    simulator.answer(DecisionAnswer(decision_point_id='dp.ofe', answer='Contractor-supplied'))
    events = list(simulator.run())

    assert types_of(events)[-1] == SIMULATION_COMPLETED
    assert not [e for e in events if e.type == DECISION_NEEDED]


def test_several_forks_at_one_stage_are_all_surfaced(monkeypatch):
    """Ask everything blocking at once rather than one round-trip per fork."""
    reasoner = FakeReasoner(forks_by_stage={
        'procurement': [fork('dp.ofe'), fork('dp.long_lead_unconfirmed')]
    })
    simulator = make(monkeypatch, reasoner)
    events = list(simulator.run())

    raised = {e.payload['id'] for e in events if e.type == DECISION_NEEDED}
    assert raised == {'dp.ofe', 'dp.long_lead_unconfirmed'}
    assert sorted(simulator.state.pending_decisions) == ['dp.long_lead_unconfirmed', 'dp.ofe']


def test_answering_only_some_forks_keeps_the_run_halted(monkeypatch):
    reasoner = FakeReasoner(forks_by_stage={
        'procurement': [fork('dp.ofe'), fork('dp.long_lead_unconfirmed')]
    })
    simulator = make(monkeypatch, reasoner)
    list(simulator.run())
    simulator.answer(DecisionAnswer(decision_point_id='dp.ofe', answer='OFE'))
    events = list(simulator.run())

    assert simulator.is_halted is True
    assert types_of(events)[-1] == SIMULATION_HALTED


def test_non_blocking_fork_does_not_halt(monkeypatch):
    """§4: only genuine forks halt; soft uncertainty is Tier-2 and flows on."""
    reasoner = FakeReasoner(
        forks_by_stage={'procurement': [fork('dp.soft', blocking=False)]}
    )
    simulator = make(monkeypatch, reasoner)
    events = list(simulator.run())

    assert types_of(events)[-1] == SIMULATION_COMPLETED
    assert simulator.is_halted is False


def test_answering_an_unknown_decision_is_rejected(monkeypatch):
    simulator = make(monkeypatch, FakeReasoner())
    with pytest.raises(KeyError, match='not an open decision'):
        simulator.answer(DecisionAnswer(decision_point_id='dp.nope', answer='x'))


def test_a_reasoning_failure_stops_the_walk_rather_than_leaving_a_gap(monkeypatch):
    reasoner = FakeReasoner(raises_at='mep_power')
    simulator = make(monkeypatch, reasoner)
    events = list(simulator.run())

    assert types_of(events)[-1] == SIMULATION_ERROR
    assert 'gateway exploded' in events[-1].payload['error']
    assert 'commissioning' not in {c[0] for c in reasoner.calls}


# --------------------------------------------------------------- resumable state


def test_halted_state_is_plain_data_so_a_run_survives_its_socket(monkeypatch):
    """The reason stop-and-ask is a state machine and not a suspended coroutine."""
    reasoner = FakeReasoner(forks_by_stage={'procurement': [fork('dp.ofe')]})
    simulator = make(monkeypatch, reasoner)
    list(simulator.run())

    snapshot = simulator.state.model_dump_json()
    assert 'dp.ofe' in snapshot
    assert simulator.state.completed_stages == ['design']
    assert simulator.state.halted_at == 'procurement'


def test_registry_holds_runs_and_can_drop_them():
    simulator = Simulator(BRIEF, run_id='run-x')
    registry.add(simulator)
    assert registry.get('run-x') is simulator
    registry.drop('run-x')
    assert registry.get('run-x') is None


# ------------------------------------------------------------------- /ws endpoint


def ws_reasoner(monkeypatch, **kwargs):
    reasoner = FakeReasoner(**kwargs)
    monkeypatch.setattr('backend.app.simulator.runner.reason_stage', reasoner)
    monkeypatch.setattr(
        'backend.app.main.build_simulator',
        lambda brief, run_id: Simulator(brief, run_id=run_id, stages=WALK),
    )
    return reasoner


def test_ws_streams_a_whole_simulation(monkeypatch):
    ws_reasoner(monkeypatch)
    with TestClient(app).websocket_connect('/ws/simulate') as ws:
        ws.send_json({'action': 'start', 'brief': BRIEF})
        received = []
        while True:
            event = ws.receive_json()
            received.append(event)
            if event['type'] in (SIMULATION_COMPLETED, SIMULATION_HALTED, SIMULATION_ERROR):
                break

    assert received[0]['type'] == SIMULATION_STARTED
    assert received[-1]['type'] == SIMULATION_COMPLETED
    assert {e['type'] for e in received} >= {STAGE_STARTED, ACTIVITY_ADDED, STAGE_COMPLETED}


def test_ws_halts_and_resumes_on_an_answer(monkeypatch):
    """The full stop-and-ask round trip over the socket."""
    ws_reasoner(monkeypatch, forks_by_stage={'procurement': [fork('dp.ofe')]})

    with TestClient(app).websocket_connect('/ws/simulate') as ws:
        ws.send_json({'action': 'start', 'brief': BRIEF})
        first = []
        while True:
            event = ws.receive_json()
            first.append(event)
            if event['type'] in (SIMULATION_HALTED, SIMULATION_COMPLETED):
                break

        assert first[-1]['type'] == SIMULATION_HALTED
        needed = next(e for e in first if e['type'] == DECISION_NEEDED)
        assert needed['payload']['id'] == 'dp.ofe'

        ws.send_json({
            'action': 'answer', 'decision_point_id': 'dp.ofe', 'answer': 'Owner-furnished',
        })
        second = []
        while True:
            event = ws.receive_json()
            second.append(event)
            if event['type'] in (SIMULATION_COMPLETED, SIMULATION_HALTED):
                break

    assert second[0]['type'] == DECISION_RESOLVED
    assert second[0]['payload']['answer'] == 'Owner-furnished'
    assert second[-1]['type'] == SIMULATION_COMPLETED


def test_ws_rejects_an_answer_to_an_unraised_fork(monkeypatch):
    ws_reasoner(monkeypatch, forks_by_stage={'procurement': [fork('dp.ofe')]})
    with TestClient(app).websocket_connect('/ws/simulate') as ws:
        ws.send_json({'action': 'start', 'brief': BRIEF})
        while ws.receive_json()['type'] != SIMULATION_HALTED:
            pass
        ws.send_json({'action': 'answer', 'decision_point_id': 'dp.wrong', 'answer': 'x'})
        error = ws.receive_json()

    assert error['type'] == SIMULATION_ERROR
    assert 'not an open decision' in error['payload']['error']


def test_ws_can_attach_to_a_halted_run_after_a_dropped_socket(monkeypatch):
    """A planner may answer minutes later, from a reconnected client."""
    ws_reasoner(monkeypatch, forks_by_stage={'procurement': [fork('dp.ofe')]})
    client = TestClient(app)

    run_id = ''
    with client.websocket_connect('/ws/simulate') as ws:
        ws.send_json({'action': 'start', 'brief': BRIEF})
        while True:
            event = ws.receive_json()
            # Take the id off the wire, as a real client does. Ids are random now (a counter
            # would hand a post-restart run the id of a stored one), so guessing 'run-1' here
            # was only ever testing the counter.
            run_id = run_id or event['payload'].get('run_id', '')
            if event['type'] == SIMULATION_HALTED:
                break
    assert run_id
    # Socket closed with the fork still open.

    with client.websocket_connect('/ws/simulate') as ws:
        ws.send_json({'action': 'attach', 'run_id': run_id})
        # Attach replays the open forks first, so the reconnected client knows what it is being
        # asked, then reports the halt with the plan built so far.
        replayed = ws.receive_json()
        assert replayed['type'] == DECISION_NEEDED
        assert replayed['payload']['id'] == 'dp.ofe'
        attached = ws.receive_json()
        assert attached['type'] == SIMULATION_HALTED
        assert attached['payload']['pending'] == ['dp.ofe']
        assert attached['payload']['output']['project_meta']['run_id'] == run_id

        ws.send_json({'action': 'answer', 'decision_point_id': 'dp.ofe', 'answer': 'OFE'})
        final = []
        while True:
            event = ws.receive_json()
            final.append(event)
            if event['type'] in (SIMULATION_COMPLETED, SIMULATION_HALTED):
                break

    assert final[-1]['type'] == SIMULATION_COMPLETED


def test_ws_can_attach_to_a_halted_run_after_a_process_restart(monkeypatch):
    """The deploy case: the container that started the run is gone before the fork is answered.

    Identical to the dropped-socket test except for one line — `registry.clear()`, which empties
    the in-memory cache and leaves storage alone, exactly as a restart does. Before runs were
    persisted this failed with "No run ...", and a planner's half-finished plan went with it.
    """
    ws_reasoner(monkeypatch, forks_by_stage={'procurement': [fork('dp.ofe')]})
    client = TestClient(app)

    run_id = ''
    with client.websocket_connect('/ws/simulate') as ws:
        ws.send_json({'action': 'start', 'brief': BRIEF})
        while True:
            event = ws.receive_json()
            run_id = run_id or event['payload'].get('run_id', '')
            if event['type'] == SIMULATION_HALTED:
                break

    registry.clear()  # <- the restart
    assert len(registry) == 0

    with client.websocket_connect('/ws/simulate') as ws:
        ws.send_json({'action': 'attach', 'run_id': run_id})
        replayed = ws.receive_json()
        assert replayed['type'] == DECISION_NEEDED, (
            'the rebuilt run did not know what it was halted on'
        )
        assert ws.receive_json()['type'] == SIMULATION_HALTED

        ws.send_json({'action': 'answer', 'decision_point_id': 'dp.ofe', 'answer': 'OFE'})
        final = []
        while True:
            event = ws.receive_json()
            final.append(event)
            if event['type'] in (SIMULATION_COMPLETED, SIMULATION_HALTED):
                break

    assert final[-1]['type'] == SIMULATION_COMPLETED, (
        'the run did not finish after being rebuilt from storage'
    )


def test_ws_attach_to_unknown_run_errors(monkeypatch):
    ws_reasoner(monkeypatch)
    with TestClient(app).websocket_connect('/ws/simulate') as ws:
        ws.send_json({'action': 'attach', 'run_id': 'run-does-not-exist'})
        event = ws.receive_json()
    assert event['type'] == SIMULATION_ERROR


def test_ws_stop_closes_and_drops_the_run(monkeypatch):
    ws_reasoner(monkeypatch)
    with TestClient(app).websocket_connect('/ws/simulate') as ws:
        ws.send_json({'action': 'start', 'brief': BRIEF})
        while ws.receive_json()['type'] != SIMULATION_COMPLETED:
            pass
        ws.send_json({'action': 'stop'})
        # Wait for the server to act on it. `stop` makes the server drop the run and close the
        # connection, so the close IS the acknowledgement - and reading until it arrives is the
        # difference between asserting the contract and racing it. Leaving the `with` block
        # immediately closes from the client side instead, and on a slower machine the socket
        # was gone before the server read the message: CI failed here with `assert 1 == 0` while
        # every local run passed.
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert len(registry) == 0


def test_simulation_started_is_emitted_once_even_when_the_first_stage_halts(monkeypatch):
    """Regression: a resumed run must not re-announce itself as started.

    Inferring "has started" from completed_stages was wrong for the case that matters most - a
    fork on the FIRST stage completes none, so a resume re-emitted simulation_started and a
    client would reset its view mid-run.
    """
    reasoner = FakeReasoner(forks_by_stage={'design': [fork('dp.tier_topology')]})
    simulator = make(monkeypatch, reasoner)

    first = list(simulator.run())
    assert simulator.state.completed_stages == [], 'the first stage itself halted'
    assert types_of(first).count(SIMULATION_STARTED) == 1

    simulator.answer(DecisionAnswer(decision_point_id='dp.tier_topology', answer='N+1'))
    resumed = list(simulator.run())

    assert SIMULATION_STARTED not in types_of(resumed)
    assert types_of(resumed)[0] == DECISION_RESOLVED
    assert types_of(resumed)[-1] == SIMULATION_COMPLETED


def test_simulation_started_announces_the_site_plan(monkeypatch):
    """The 3D model needs geometry before any stage runs (Task 16).

    Zones are a deterministic function of load and topology, so they are known the moment the
    brief is confirmed. They used to travel only with the authoritative SimulationOutput, which
    arrives at a settle point - so the 3D view had nothing to draw for most of a run and then
    appeared complete in one jump, the opposite of watching a plan get built.
    """
    ws_reasoner(monkeypatch)
    simulator = Simulator(BRIEF, stages=['substructure'])
    started = next(iter(simulator.run()))

    assert started.type == SIMULATION_STARTED
    zones = started.payload.get('zones')
    assert zones, 'no site plan announced at the start of the run'
    assert {'id', 'kind', 'stage'} <= set(zones[0])
    # Every zone names the stage that brings it into existence: that mapping is what lets the
    # model raise each one in step with the walk.
    assert all(z.get('stage') for z in zones)


def test_the_announced_site_plan_matches_what_assembly_produces(monkeypatch):
    """Two sources for the same zones would drift. The streamed plan must BE the final plan."""
    ws_reasoner(monkeypatch)
    simulator = Simulator(BRIEF, stages=WALK)
    announced = next(iter(simulator.run())).payload['zones']

    run_to_completion(simulator, {})
    assembled = simulator.result.zones

    assert [z['id'] for z in announced] == [z['id'] for z in assembled]


# ------------------------------------------------- what "pending" means on the wire


def test_open_decisions_exclude_answered_ones():
    """`pending_decisions` is not the set of open forks, and the difference is load-bearing.

    An entry stays in the map until `run()` emits decision_resolved, so between answering a fork
    and resuming the walk the map still contains it. Anything telling a client what is
    outstanding must say `open_decision_ids` instead.
    """
    state = RunState(run_id='r')
    state.pending_decisions = {'dp.a': {}, 'dp.b': {}}
    state.answers = {'dp.a': {'answer': 'yes'}}

    assert state.open_decision_ids == ['dp.b']
    # is_halted deliberately still reads the raw map: the socket handler relies on a run staying
    # halted between answering one fork of a stage and answering the last.
    assert state.is_halted is True


def test_a_resumed_halt_reports_only_the_forks_still_open(monkeypatch):
    """Pins the invariant on the streaming path, where it already held.

    Worth being precise about, because the obvious story is wrong. `run()` pops answered
    decisions out of `pending_decisions` at the top of every resume, before it can halt again -
    so the halted event emitted by the runner never listed an answered fork, and swapping it to
    `open_decision_ids` changed nothing here. Reverting that line leaves this test green, and it
    is not claimed as a regression test for it.

    The divergence that actually killed two live runs was in the ATTACH path, which builds its
    payload from restored state before `run()` has popped anything - covered by
    test_attach_re_raises_only_the_forks_still_open, which does fail when reverted.
    """
    reasoner = FakeReasoner(forks_by_stage={
        'procurement': [fork('dp.ofe'), fork('dp.delivery_mode')],
    })
    ws_reasoner_like = make(monkeypatch, reasoner)

    events = list(ws_reasoner_like.run())
    halted = [e for e in events if e.type == SIMULATION_HALTED]
    assert halted, 'the run did not halt'
    assert set(halted[-1].payload['pending']) == {'dp.ofe', 'dp.delivery_mode'}

    # Answer ONE of the two. The stage is still halted, but that fork is no longer open.
    ws_reasoner_like.answer(DecisionAnswer(decision_point_id='dp.ofe', answer='Owner-furnished'))
    assert ws_reasoner_like.state.is_halted, 'still waiting on the second fork'

    # The payload is what matters, not the property: resume, and the halted event the client
    # receives must no longer list the fork it just answered. Asserting the property alone
    # passed whether or not the payload used it, so it was not earning its place.
    again = list(ws_reasoner_like.run())
    halted_again = [e for e in again if e.type == SIMULATION_HALTED]
    assert halted_again, 'the run should still be halted on the second fork'
    assert halted_again[-1].payload['pending'] == ['dp.delivery_mode'], (
        f"simulation_halted still reports the answered fork: "
        f"{halted_again[-1].payload['pending']}"
    )


def test_attach_re_raises_only_the_forks_still_open(monkeypatch):
    """Reconnecting must not invite the client to answer something already answered."""
    reasoner = FakeReasoner(forks_by_stage={
        'procurement': [fork('dp.ofe'), fork('dp.delivery_mode')],
    })
    monkeypatch.setattr('backend.app.simulator.runner.reason_stage', reasoner)
    monkeypatch.setattr(
        'backend.app.main.build_simulator',
        lambda brief, run_id: Simulator(BRIEF, run_id=run_id, stages=WALK),
    )

    with TestClient(app).websocket_connect('/ws/simulate') as ws:
        ws.send_json({'action': 'start', 'brief': BRIEF})
        run_id = ''
        while True:
            event = ws.receive_json()
            if event['type'] == SIMULATION_STARTED:
                run_id = event['payload']['run_id']
            if event['type'] == SIMULATION_HALTED:
                break
        ws.send_json({'action': 'answer', 'decision_point_id': 'dp.ofe',
                      'answer': 'Owner-furnished'})
        ws.receive_json()  # decision_recorded: one fork still open

    # Reconnect, as a client whose socket dropped would.
    with TestClient(app).websocket_connect('/ws/simulate') as ws:
        ws.send_json({'action': 'attach', 'run_id': run_id})
        raised, payload = [], None
        while True:
            event = ws.receive_json()
            if event['type'] == DECISION_NEEDED:
                raised.append(event['payload']['id'])
            elif event['type'] in (SIMULATION_HALTED, SIMULATION_STARTED):
                payload = event['payload']
                break

    assert 'dp.ofe' not in raised, 'attach re-raised a fork that was already answered'
    assert raised == ['dp.delivery_mode']
    assert payload['pending'] == ['dp.delivery_mode']
