"""Registry of live simulation runs, backed by durable storage.

A halted run must outlive the socket that was watching it: a planner may answer a fork minutes
later, from a reconnected client. It must also outlive the *process* — a deploy or a platform
restart used to destroy every open run, discarding reasoning the user had already paid model
credits for and stranding them in front of a graph that could never continue.

So the registry is now a write-through cache. Memory serves the common case (same process, socket
still open); `RunStore` makes the run survive anything that ends the process. A `get` that misses
in memory falls back to storage and rehydrates, which is exactly the reconnect-after-restart path.

Storage failures degrade to the old in-memory behaviour rather than failing the run; see
`simulator/store.py`.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from backend.app.simulator.runner import Simulator
from backend.app.simulator.store import RunStore


class RunRegistry:
    """Holds live runs by id, writing each one through to storage as it advances."""

    def __init__(self, store: Optional[RunStore] = None) -> None:
        self._runs: Dict[str, Simulator] = {}
        self._store = store if store is not None else RunStore()

    def new_id(self) -> str:
        """A collision-proof id.

        Deliberately not a sequential counter any more: a counter restarts at 1 with the
        process, so the first run after a restart would claim the id of a stored run that is
        still open and resume someone else's simulation.
        """
        return f'run-{uuid.uuid4().hex[:12]}'

    def add(self, simulator: Simulator) -> str:
        self._runs[simulator.state.run_id] = simulator
        self._store.save(simulator)
        return simulator.state.run_id

    def save(self, simulator: Simulator) -> bool:
        """Checkpoint a run after it advances. Called at every settle point of the walk."""
        self._runs[simulator.state.run_id] = simulator
        return self._store.save(simulator)

    def get(self, run_id: str, *, adapter: Any = None) -> Optional[Simulator]:
        simulator = self._runs.get(run_id)
        if simulator is not None:
            return simulator
        # Memory miss: either a different worker, or this process is new. Rehydrate.
        restored = self._store.load(run_id, adapter=adapter)
        if restored is not None:
            self._runs[run_id] = restored
        return restored

    def drop(self, run_id: str) -> None:
        self._runs.pop(run_id, None)
        self._store.drop(run_id)

    def clear(self) -> None:
        """Forget every in-memory run, keeping storage intact.

        This IS a simulated process restart, and is used as one in the tests.
        """
        self._runs.clear()

    def reset(self) -> None:
        """Forget everything, storage included. Test isolation only."""
        self._runs.clear()
        self._store.purge()

    def __len__(self) -> int:
        return len(self._runs)


registry = RunRegistry()
