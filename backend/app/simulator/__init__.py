"""The simulator: graph walk, event stream, stop-and-ask.

SIMULATION_AND_REASONING.md sections 2-4. The simulator is the heart of the product: it produces
one SimulationOutput that the 2D view, the 3D/4D view and the P6 export all project from.
"""

from __future__ import annotations

from backend.app.simulator.events import (
    ACTIVITY_ADDED,
    DECISION_NEEDED,
    DECISION_RESOLVED,
    EVENT_TYPES,
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
    SimulationEvent,
)
from backend.app.simulator.output import build_simulation_output
from backend.app.simulator.registry import RunRegistry, registry
from backend.app.simulator.runner import Simulator, run_to_completion

__all__ = [
    'ACTIVITY_ADDED',
    'DECISION_NEEDED',
    'DECISION_RESOLVED',
    'EVENT_TYPES',
    'GATE_INSERTED',
    'PACKAGE_EXPANDED',
    'SIMULATION_COMPLETED',
    'SIMULATION_ERROR',
    'SIMULATION_HALTED',
    'SIMULATION_STARTED',
    'SPEC_EVENT_TYPES',
    'STAGE_COMPLETED',
    'STAGE_STARTED',
    'DecisionAnswer',
    'RunRegistry',
    'build_simulation_output',
    'RunState',
    'SimulationEvent',
    'Simulator',
    'registry',
    'run_to_completion',
]
