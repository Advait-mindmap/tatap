"""Durable storage for in-flight simulation runs.

A run is a conversation, not a request. A planner may answer a fork minutes or hours after the
graph stopped at it, from a reconnected browser — and in between, the container it started on can
be replaced by a deploy, a crash, or a platform restart. Holding runs only in process memory made
every one of those events silently destroy work the user had already paid model credits for.

The simulator was built as a data state machine precisely so this would be possible
(simulator/runner.py): `RunState` and the per-stage `StageReasoning` are plain pydantic models, so
persisting a run is serialising two objects and rehydrating it is parsing them back. Nothing in
the walk changes.

**Persistence failures never fail a run.** If the database is unreachable the run continues in
memory with a warning: losing durability is bad, but refusing to plan because the store is down
would be worse, and the user's alternative is no simulation at all.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from backend.app.schemas import StageReasoning
from backend.app.simulator.events import RunState
from backend.app.simulator.runner import Simulator

log = logging.getLogger(__name__)


def _status_of(simulator: Simulator) -> str:
    if simulator.is_halted:
        return 'halted'
    if simulator.state.is_complete:
        return 'complete'
    return 'running'


class RunStore:
    """Reads and writes `PersistedRun` rows. One row per run, overwritten as the walk advances."""

    def __init__(self, session_factory: Any = None) -> None:
        self._session_factory = session_factory

    def _session(self):
        if self._session_factory is not None:
            return self._session_factory()
        from backend.app.database import SessionLocal, ensure_tables

        ensure_tables()
        return SessionLocal()

    # ------------------------------------------------------------------------------ writing

    def save(self, simulator: Simulator) -> bool:
        """Upsert the run. Returns whether it was actually persisted."""
        from backend.app.models import PersistedRun

        try:
            session = self._session()
        except Exception as exc:  # noqa: BLE001 - durability is best-effort, see module docstring
            log.warning('run %s not persisted (no session): %s', simulator.state.run_id, exc)
            return False

        try:
            with session:
                row = session.get(PersistedRun, simulator.state.run_id)
                if row is None:
                    row = PersistedRun(run_id=simulator.state.run_id)
                    session.add(row)
                row.status = _status_of(simulator)
                row.state = simulator.state.model_dump(mode='json')
                row.reasonings = {
                    stage: reasoning.model_dump(mode='json')
                    for stage, reasoning in simulator.stage_reasonings.items()
                }
                row.stages = list(simulator.stages)
                session.commit()
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning('run %s not persisted: %s', simulator.state.run_id, exc)
            return False

    # ------------------------------------------------------------------------------ reading

    def load(self, run_id: str, *, adapter: Any = None) -> Optional[Simulator]:
        """Rebuild a run from storage, or None if it was never stored.

        The rebuilt simulator is equivalent to the one that stopped, not a copy of its object
        graph: same state, same reasoning already done, same remaining stages. `run()` on it
        picks up from `completed_stages` exactly as it would have before the restart.
        """
        from backend.app.models import PersistedRun

        try:
            session = self._session()
        except Exception as exc:  # noqa: BLE001
            log.warning('cannot load run %s: %s', run_id, exc)
            return None

        try:
            with session:
                row = session.get(PersistedRun, run_id)
                if row is None:
                    return None
                payload: Dict[str, Any] = dict(row.state or {})
                reasonings = dict(row.reasonings or {})
                stages = list(row.stages or []) or None
        except Exception as exc:  # noqa: BLE001
            log.warning('cannot load run %s: %s', run_id, exc)
            return None

        state = RunState(**payload)
        simulator = Simulator(
            state.brief,
            run_id=state.run_id,
            adapter=adapter,
            stages=stages,
            state=state,
        )
        simulator.stage_reasonings = {
            stage: StageReasoning(**data) for stage, data in reasonings.items()
        }
        # Rebuild the emitted-id sets from the reasoning we just restored. Without this a
        # resumed run could re-emit gates a client has already drawn; completed stages are
        # skipped by the walk, so activities are covered, but gates are keyed separately.
        simulator._emitted_gate_ids = {
            gate.gate_id
            for reasoning in simulator.stage_reasonings.values()
            for gate in reasoning.gates
        }
        return simulator

    def drop(self, run_id: str) -> None:
        from backend.app.models import PersistedRun

        try:
            session = self._session()
            with session:
                row = session.get(PersistedRun, run_id)
                if row is not None:
                    session.delete(row)
                    session.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning('could not drop run %s: %s', run_id, exc)

    def purge(self) -> None:
        """Delete every stored run. Test isolation only — never called by the app."""
        from backend.app.models import PersistedRun

        try:
            session = self._session()
            with session:
                session.query(PersistedRun).delete()
                session.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning('could not purge runs: %s', exc)
