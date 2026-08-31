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
    # Procurement sits here per DOMAIN_KNOWLEDGE.md §2, which orders the departments
    # Statutory/Liaison (3) -> Procurement/SCM (4) -> Civil/Structure (6), and per
    # SIMULATION_AND_REASONING.md §2, which walks engineering -> procurement -> construction ->
    # commissioning. Its WALK POSITION is early because that is when procurement strategy and
    # long-lead exposure must be reasoned about; its ACTIVITIES (RFQ -> award -> manufacture ->
    # FAT -> ship -> deliver) span the programme and land as delivery milestones that gate
    # construction. DOMAIN_KNOWLEDGE.md §4: "Front-load it; tie construction to delivery."
    'procurement',
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

#: The procurement stage owns the long-lead register directly rather than deriving it from
#: fragnets. Named so the special case in gather_stage_libraries() is not a bare string.
PROCUREMENT_STAGE = 'procurement'

#: Owning department per stage, from the DOMAIN_KNOWLEDGE.md §3 table.
STAGE_DEPARTMENT: Dict[str, str] = {
    'approvals': 'liaison',
    'procurement': 'procurement',
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
#
# DOCUMENTED FOR THE DOMAIN TEAM in docs/ASSUMPTIONS_AWAITING_VERIFICATION.md §1, which explains
# each row's reasoning and records the open challenges. Keep that note in step with this table.
# The first challenge it raised — that no procurement stage existed, so the long-lead fork that
# sets RFS was not raised until mep_power — has since been resolved by adding that stage.
# ---------------------------------------------------------------------------------------------
DECISION_TAGS_BY_STAGE: Dict[str, FrozenSet[str]] = {
    'approvals': frozenset({'statutory', 'design', 'planning'}),
    # The 'procurement' tag lives here rather than on the MEP stages. Before the procurement
    # stage existed it hung off mep_power/mep_cooling, which meant dp.long_lead_unconfirmed -
    # the fork about the lead times that set RFS - was not raised until construction was already
    # being sequenced. Note this ADDS an early firing point; it does not remove the
    # per-discipline firings, because dp.delivery_mode also carries civil/mep/fit_out tags and
    # is deliberately still asked per discipline (DOMAIN_KNOWLEDGE.md §6).
    'procurement': frozenset({'procurement'}),
    'enabling': frozenset({'enabling'}),
    'substructure': frozenset({'civil'}),
    'superstructure': frozenset({'civil'}),
    'envelope': frozenset({'civil'}),
    'mep_power': frozenset({'mep'}),
    'mep_cooling': frozenset({'mep'}),
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
