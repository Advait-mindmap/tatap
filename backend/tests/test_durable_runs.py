"""A run must survive the process it started on.

The registry used to be memory only, so a Railway deploy or a crash destroyed every open run —
including reasoning the user had already paid model credits for. These tests hold the line by
simulating the restart the way the failure actually happens: the in-memory cache is emptied
while storage is left alone, and the run has to come back from storage alone.

`registry.clear()` IS the simulated restart (see simulator/registry.py) — nothing here reaches
into the store to help it along.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.models import Base, PersistedRun
from backend.app.simulator import DecisionAnswer, Simulator, registry
from backend.app.simulator.events import SIMULATION_COMPLETED, SIMULATION_HALTED
from backend.app.simulator.registry import RunRegistry
from backend.app.simulator.store import RunStore

BRIEF = {
    'project_name': 'Durability DC',
    'city': 'Navi Mumbai',
    'tier': 'III',
    'it_load_mw': 12.0,
    'redundancy_topology': '2N',
}


@pytest.fixture()
def store(tmp_path):
    """A store on its own throwaway database, so these tests never touch the app's."""
    engine = create_engine(f'sqlite:///{tmp_path / "runs.db"}', future=True)
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return RunStore(session_factory=factory), factory


def _halted_simulator(stages=('design', 'approvals')) -> Simulator:
    """A run walked until a genuine fork stops it, using the mechanical stub for determinism."""
    from backend.app.llm_stub import StubAdapter

    simulator = Simulator(BRIEF, run_id='run-durable', adapter=StubAdapter(), stages=list(stages))
    events = list(simulator.run())
    assert any(e.type == SIMULATION_HALTED for e in events), (
        'this fixture needs a run that halts; if the stub stopped raising forks, pick a stage '
        'that still does rather than weakening the assertion'
    )
    return simulator


def test_run_survives_a_simulated_restart(store):
    """The whole point: stop at a fork, lose the process, answer, and finish the same run."""
    run_store, factory = store
    before = _halted_simulator()
    open_forks = sorted(before.state.pending_decisions)
    stages_done = list(before.state.completed_stages)
    seq_before = before.state.seq
    reasoned = sorted(before.stage_reasonings)

    reg = RunRegistry(store=run_store)
    reg.add(before)

    # --- the restart. Memory is gone; only what was written survives.
    reg.clear()
    assert len(reg) == 0
    del before

    after = reg.get('run-durable')
    assert after is not None, 'the run was lost by a restart — exactly the bug being fixed'
    assert sorted(after.state.pending_decisions) == open_forks
    assert after.state.completed_stages == stages_done
    assert after.state.seq == seq_before
    assert after.state.started is True
    assert after.state.brief == BRIEF

    # The reasoning already paid for came back too. Without this the resumed run would call the
    # model again for stages it had already reasoned, which is the expensive half of the bug.
    assert sorted(after.stage_reasonings) == reasoned

    # And it is a live simulator, not a read-only snapshot: answering resumes the walk.
    from backend.app.llm_stub import StubAdapter

    after.adapter = StubAdapter()
    for fork in open_forks:
        after.answer(DecisionAnswer(decision_point_id=fork, answer='Confirmed'))
    events = list(after.run())
    assert [e.type for e in events], 'the resumed run produced no events'
    assert after.state.completed_stages != stages_done, 'the walk did not advance after resuming'


def test_answers_given_before_a_restart_are_not_lost(store):
    """A planner who answers and then hits a deploy should not be asked the same fork twice."""
    run_store, factory = store
    simulator = _halted_simulator()
    fork = sorted(simulator.state.pending_decisions)[0]
    simulator.answer(DecisionAnswer(decision_point_id=fork, answer='Self-perform'))

    reg = RunRegistry(store=run_store)
    reg.save(simulator)
    reg.clear()

    restored = reg.get('run-durable')
    assert restored is not None
    assert restored.state.answers[fork]['answer'] == 'Self-perform'
    # why_stuck and options survive too, so the audit trail is not thinned by a restart.
    assert 'why_stuck' in restored.state.answers[fork]


def test_a_completed_run_is_stored_as_complete(store):
    """Status is written for operators reading the table, and reflects the walk's real state."""
    from backend.app.llm_stub import StubAdapter

    run_store, factory = store
    simulator = Simulator(
        BRIEF, run_id='run-complete', adapter=StubAdapter(), stages=['design']
    )
    # Drive it properly rather than skipping when it halts: a skipped assertion is how a
    # "passing" suite ends up proving nothing, which has already bitten this project twice.
    events = []
    for _ in range(10):
        events.extend(simulator.run())
        if not simulator.is_halted:
            break
        for fork in sorted(simulator.state.pending_decisions):
            simulator.answer(DecisionAnswer(decision_point_id=fork, answer='Confirmed'))
    assert any(e.type == SIMULATION_COMPLETED for e in events)

    run_store.save(simulator)
    with factory() as session:
        row = session.get(PersistedRun, 'run-complete')
        assert row is not None
        assert row.status == 'complete'


def test_dropping_a_run_removes_it_from_storage_too(store):
    """Otherwise `stop` would leave a run that a later `attach` could resurrect."""
    run_store, factory = store
    simulator = _halted_simulator()
    reg = RunRegistry(store=run_store)
    reg.add(simulator)

    reg.drop('run-durable')
    reg.clear()
    assert reg.get('run-durable') is None
    with factory() as session:
        assert session.get(PersistedRun, 'run-durable') is None


def test_persistence_failure_does_not_kill_the_run():
    """Durability is best-effort: a store that is down degrades to memory, it does not fail."""

    class DeadStore(RunStore):
        def _session(self):
            raise RuntimeError('database is down')

    from backend.app.llm_stub import StubAdapter

    reg = RunRegistry(store=DeadStore())
    simulator = Simulator(BRIEF, run_id='run-nostore', adapter=StubAdapter(), stages=['design'])
    reg.add(simulator)  # must not raise
    assert reg.get('run-nostore') is simulator  # still served from memory
    reg.clear()
    assert reg.get('run-nostore') is None  # and honestly reports the loss


def test_new_ids_do_not_restart_at_one_after_a_process_restart():
    """A sequential counter would hand a fresh run the id of a stored, still-open one."""
    first = RunRegistry(store=RunStore(session_factory=None)).new_id()
    second = RunRegistry(store=RunStore(session_factory=None)).new_id()
    assert first != second
    assert not first.endswith('-1')
