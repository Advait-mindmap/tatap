"""The deterministic assembly engine.

CLAUDE.md rule 2: the LLM reasons and selects within the libraries; this layer instances the
activities, logic, durations and counts. NO LLM CALL HAPPENS HERE - assembly is a pure function
of (stage reasoning, brief, libraries), so the same inputs always produce identical output.
"""

from __future__ import annotations

from backend.app.engine.assemble import (
    STAGE_DISCIPLINE,
    STAGE_ZONE_KIND,
    assemble,
    match_safety_rules,
)
from backend.app.engine.gates import (
    CROSS_STAGE_GATES,
    DELIVERY_GATE,
    GateRule,
    cross_stage_gate_id,
    delivery_gate_id,
    material_link_index,
)
from backend.app.engine.ids import activity_id, gate_id, hold_point_id, wbs_id, zone_id
from backend.app.engine.zones import ZONE_FIRST_STAGE, generate_zones

__all__ = [
    'CROSS_STAGE_GATES',
    'DELIVERY_GATE',
    'GateRule',
    'STAGE_DISCIPLINE',
    'STAGE_ZONE_KIND',
    'ZONE_FIRST_STAGE',
    'activity_id',
    'assemble',
    'cross_stage_gate_id',
    'delivery_gate_id',
    'gate_id',
    'generate_zones',
    'hold_point_id',
    'match_safety_rules',
    'material_link_index',
    'wbs_id',
    'zone_id',
]
