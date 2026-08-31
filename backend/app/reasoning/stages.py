"""The stage sequence the simulator walks, and how library vocabularies map onto it.

DOMAIN_KNOWLEDGE.md §2 walks the build department by department; §3 tables the stages. The
simulator walks these in execution order: engineering -> procurement -> construction ->
commissioning (SIMULATION_AND_REASONING.md §2).
"""

from __future__ import annotations

from typing import Dict, FrozenSet, List

#: Canonical stages in execution order. Derived from the DOMAIN_KNOWLEDGE.md §3 stage table.
STAGES: List[str] = [
    'approvals',
    'enabling',
    'substructure',
    'superstructure',
    'envelope',
    'mep_power',
    'mep_cooling',
    'fire_bms',
    'fit_out',
    'commissioning',
    'handover',
]

STAGE_INDEX: Dict[str, int] = {stage: i for i, stage in enumerate(STAGES)}

#: Owning department per stage, from the DOMAIN_KNOWLEDGE.md §3 table.
STAGE_DEPARTMENT: Dict[str, str] = {
    'approvals': 'liaison',
    'enabling': 'construction',
    'substructure': 'civil',
    'superstructure': 'civil',
    'envelope': 'construction',
    'mep_power': 'mep',
    'mep_cooling': 'mep',
    'fire_bms': 'subcontract',
    'fit_out': 'construction',
    'commissioning': 'commissioning',
    'handover': 'pm',
}

# ---------------------------------------------------------------------------------------------
# MAPPING INTRODUCED HERE, NOT TAKEN FROM THE SPEC — needs domain review.
#
# The decision-point library tags each fork with `applies_to_stages` using a department-flavoured
# vocabulary ('civil', 'mep', 'procurement', 'statutory') while fragnets use construction stages
# ('substructure', 'mep_power'). Nothing in /docs reconciles the two, so this table is my
# reading, not the client's. Getting it wrong means a genuine fork is raised at the wrong stage
# or missed entirely, so it belongs in the admin decision-point editor (ADMIN_SPEC.md §3) rather
# than hard-coded here long-term.
# ---------------------------------------------------------------------------------------------
DECISION_TAGS_BY_STAGE: Dict[str, FrozenSet[str]] = {
    'approvals': frozenset({'statutory', 'design', 'planning'}),
    'enabling': frozenset({'enabling'}),
    'substructure': frozenset({'civil'}),
    'superstructure': frozenset({'civil'}),
    'envelope': frozenset({'civil'}),
    'mep_power': frozenset({'mep', 'procurement'}),
    'mep_cooling': frozenset({'mep', 'procurement'}),
    'fire_bms': frozenset({'mep', 'fit_out'}),
    'fit_out': frozenset({'fit_out'}),
    'commissioning': frozenset({'commissioning'}),
    'handover': frozenset({'handover'}),
}


def is_valid_stage(stage: str) -> bool:
    return stage in STAGE_INDEX


def stages_in_order() -> List[str]:
    return list(STAGES)


def decision_tags_for(stage: str) -> FrozenSet[str]:
    return DECISION_TAGS_BY_STAGE.get(stage, frozenset())
