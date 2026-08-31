"""In-memory registry of live simulation runs.

A halted run must outlive the socket that was watching it: a planner may answer a fork minutes
later, from a reconnected client. Keeping runs here means /ws can resume one rather than
restarting the walk.

In-memory is deliberate for this task and NOT durable - a process restart loses open runs. The
data model already has the tables to persist runs (simulations, decisions), so promoting this to
Postgres is a later change with no effect on the runner, which keeps its state as plain data
precisely so it can be stored.
"""

from __future__ import annotations

import itertools
from typing import Dict, Iterator, Optional

from backend.app.simulator.runner import Simulator


class RunRegistry:
    """Holds live runs by id. Ids are sequential, so they are stable in tests."""

    def __init__(self) -> None:
        self._runs: Dict[str, Simulator] = {}
        self._counter: Iterator[int] = itertools.count(1)

    def new_id(self) -> str:
        return f'run-{next(self._counter)}'

    def add(self, simulator: Simulator) -> str:
        self._runs[simulator.state.run_id] = simulator
        return simulator.state.run_id

    def get(self, run_id: str) -> Optional[Simulator]:
        return self._runs.get(run_id)

    def drop(self, run_id: str) -> None:
        self._runs.pop(run_id, None)

    def clear(self) -> None:
        self._runs.clear()
        self._counter = itertools.count(1)

    def __len__(self) -> int:
        return len(self._runs)


registry = RunRegistry()
