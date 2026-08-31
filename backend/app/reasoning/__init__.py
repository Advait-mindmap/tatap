"""The per-stage expert reasoning loop.

SIMULATION_AND_REASONING.md sections 3, 5 and 6. The LLM reasons and selects within the
retrieved corpus and libraries and cites what it used; the deterministic engine instances the
activities, logic and durations (CLAUDE.md rule 2).
"""

from __future__ import annotations

from backend.app.reasoning.loop import (
    UNVERIFIED_CONFIDENCE_CAP,
    build_stage_reasoning,
    conf_threshold,
    gather_stage_libraries,
    reason_stage,
    retrieve_for_stage,
)
from backend.app.reasoning.prompt import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_user_prompt,
    reasoning_schema,
)
from backend.app.reasoning.stages import (
    DECISION_TAGS_BY_STAGE,
    STAGE_DEPARTMENT,
    STAGES,
    decision_tags_for,
    is_valid_stage,
    stages_in_order,
)

__all__ = [
    'DECISION_TAGS_BY_STAGE',
    'PROMPT_VERSION',
    'STAGES',
    'STAGE_DEPARTMENT',
    'SYSTEM_PROMPT',
    'UNVERIFIED_CONFIDENCE_CAP',
    'build_stage_reasoning',
    'build_user_prompt',
    'conf_threshold',
    'decision_tags_for',
    'gather_stage_libraries',
    'is_valid_stage',
    'reason_stage',
    'reasoning_schema',
    'retrieve_for_stage',
    'stages_in_order',
]
