"""The simulation event stream.

SIMULATION_AND_REASONING.md §2 names the events verbatim. They are what makes the flow
"watchable one by one": the 2D view draws each node as it is emitted and the 3D model builds the
corresponding zone as its stage starts (VISUALIZATION_SPEC.md §1, §2).

Events carry a monotonic `seq` and no wall-clock time. A timestamp would make the stream
untestable for equality and would leak non-determinism into a layer that has none of its own —
the UI can stamp arrival time itself if it wants one.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# --- the eight from the spec ---------------------------------------------------------------
STAGE_STARTED = 'stage_started'
PACKAGE_EXPANDED = 'package_expanded'
ACTIVITY_ADDED = 'activity_added'
GATE_INSERTED = 'gate_inserted'
DECISION_NEEDED = 'decision_needed'
DECISION_RESOLVED = 'decision_resolved'
STAGE_COMPLETED = 'stage_completed'
SIMULATION_COMPLETED = 'simulation_completed'

# --- run-lifecycle events, so a client can render a run it did not start --------------------
SIMULATION_STARTED = 'simulation_started'
#: The walk stopped at a genuine fork and is waiting. Distinct from `simulation_completed`:
#: a halted run is unfinished, and a UI that conflated them would show a partial plan as done.
SIMULATION_HALTED = 'simulation_halted'
SIMULATION_ERROR = 'simulation_error'

EVENT_TYPES = (
    SIMULATION_STARTED,
    STAGE_STARTED,
    PACKAGE_EXPANDED,
    ACTIVITY_ADDED,
    GATE_INSERTED,
    DECISION_NEEDED,
    DECISION_RESOLVED,
    STAGE_COMPLETED,
    SIMULATION_HALTED,
    SIMULATION_COMPLETED,
    SIMULATION_ERROR,
)

#: Events the spec requires the simulator to emit. Asserted in tests so a rename cannot quietly
#: drop one the 2D/3D views depend on.
SPEC_EVENT_TYPES = (
    STAGE_STARTED, PACKAGE_EXPANDED, ACTIVITY_ADDED, GATE_INSERTED,
    DECISION_NEEDED, DECISION_RESOLVED, STAGE_COMPLETED, SIMULATION_COMPLETED,
)


class SimulationEvent(BaseModel):
    """One event in the stream."""

    seq: int
    type: str
    stage: str = ''
    payload: Dict[str, Any] = Field(default_factory=dict)

    def to_wire(self) -> Dict[str, Any]:
        return {'seq': self.seq, 'type': self.type, 'stage': self.stage, 'payload': self.payload}


class DecisionAnswer(BaseModel):
    """A planner's answer to a raised decision point."""

    decision_point_id: str
    answer: str
    answered_by: str = 'planner'
    note: str = ''


class RunState(BaseModel):
    """Everything needed to resume a halted run.

    Kept as data rather than as a suspended coroutine so a run survives a dropped socket: the
    client reconnects, and the walk picks up from `completed_stages` with the answers applied.
    """

    run_id: str
    brief: Dict[str, Any] = Field(default_factory=dict)
    #: Whether simulation_started has been emitted. Tracked explicitly rather than inferred
    #: from completed_stages, because a run that halts on its FIRST stage has completed none -
    #: and re-announcing a start would make a resuming client reset its view mid-run.
    started: bool = False
    completed_stages: List[str] = Field(default_factory=list)
    halted_at: Optional[str] = None
    pending_decisions: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    answers: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    seq: int = 0

    @property
    def open_decision_ids(self) -> List[str]:
        """Forks still waiting on an answer.

        NOT the same as `pending_decisions`, which is the subtlety this exists to hide.
        `pending_decisions` keeps an entry until `run()` emits decision_resolved, so between
        answering a fork and resuming the walk it still lists forks that HAVE been answered.

        Every wire payload that tells a client what is outstanding must use this. Sending the
        raw map meant `simulation_halted.pending` and `decision_recorded.pending` disagreed
        about what the same field name meant: one filtered, the other did not. A client that
        trusted the unfiltered one answered a resolved fork, and the server rejects that with
        "not an open decision on this run" and kills the run. Found the hard way, driving the
        deployment from a script.
        """
        return sorted(set(self.pending_decisions) - set(self.answers))

    @property
    def is_halted(self) -> bool:
        # Deliberately the RAW map, not open_decision_ids. The websocket handler relies on a run
        # staying halted between answering one fork of a multi-fork stage and answering the
        # last, so that it waits rather than resuming early.
        return bool(self.pending_decisions)

    @property
    def is_complete(self) -> bool:
        return self.halted_at is None and not self.pending_decisions and bool(
            self.completed_stages
        )
