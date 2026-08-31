"""Project one simulation into the single SimulationOutput the rest of the product reads.

SIMULATION_AND_REASONING.md §7: "one simulation -> many projections". `flow` drives the 2D view,
`zones` plus each activity's stage/zone_id drive the 3D/4D model, `activities` export to P6, and
`reasoning_trail` powers hover-to-explain. They are views of one object, not four pipelines, so
they cannot drift apart.

This module is pure: it reshapes what the simulator and engine already produced. No LLM call, no
clock, no new domain content — anything it cannot derive from its inputs is absent rather than
filled in.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

from backend.app.libraries import available_cities, load_city_pathway, load_library
from backend.app.schemas import AssemblyResult, Decision, SimulationOutput, StageReasoning

#: How an assembled activity type maps onto the node kinds the 2D view distinguishes
#: (VISUALIZATION_SPEC.md §1).
NODE_KIND_BY_TYPE = {
    'task': 'activity',
    'milestone': 'milestone',
    'gate': 'compliance_gate',
    'hold_point': 'quality_hold',
}


def _decision_nodes(
    resolved: Dict[str, Dict[str, Any]], pending: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Decision points as first-class flow nodes.

    VISUALIZATION_SPEC.md §1 calls these out prominently — this is where thought stopped — and
    §4 requires resolved ones to stay visible with their answer, so the reasoning stays auditable
    rather than disappearing once answered.
    """
    nodes: List[Dict[str, Any]] = []
    for decision_id, payload in sorted(pending.items()):
        nodes.append({
            'id': f'decision.{decision_id}',
            'kind': 'decision_point',
            'stage': payload.get('stage', ''),
            'label': payload.get('question') or decision_id,
            'dept': None,
            'trail_ref': f'trail.decision.{decision_id}',
            'zone_id': None,
            'status': 'open',
            'blocking': bool(payload.get('blocking', True)),
            'why_stuck': payload.get('why_stuck', ''),
            'options': payload.get('options', []),
            'impact': payload.get('impact', ''),
            'answer': None,
        })
    for decision_id, payload in sorted(resolved.items()):
        nodes.append({
            'id': f'decision.{decision_id}',
            'kind': 'decision_point',
            'stage': payload.get('stage', ''),
            'label': payload.get('question') or decision_id,
            'dept': None,
            'trail_ref': f'trail.decision.{decision_id}',
            'zone_id': None,
            'status': 'resolved',
            'blocking': True,
            'why_stuck': '',
            'options': [],
            'impact': payload.get('impact', ''),
            'answer': payload.get('answer'),
        })
    return nodes


def _dcma_summary(assembly: AssemblyResult) -> Dict[str, Any]:
    """A partial DCMA-style quality read.

    Deliberately partial and labelled as such: the checks that need dates (float, critical path,
    baseline execution) cannot be computed until scheduling exists, and reporting them as passing
    would be worse than reporting them as absent.
    """
    tasks = [a for a in assembly.activities if a.type == 'task']
    with_predecessors = {e.to_id for e in assembly.edges}
    with_successors = {e.from_id for e in assembly.edges}

    missing_logic = sorted(a.id for a in tasks if a.id not in with_predecessors)
    dangling = sorted(a.id for a in tasks if a.id not in with_successors)
    lags = [e for e in assembly.edges if e.lag > 0]
    leads = [e for e in assembly.edges if e.lag < 0]

    return {
        'activity_count': len(tasks),
        'total_nodes': len(assembly.activities),
        'missing_predecessor_count': len(missing_logic),
        'missing_predecessor_ids': missing_logic[:20],
        'dangling_count': len(dangling),
        'dangling_ids': dangling[:20],
        'lag_count': len(lags),
        'lead_count': len(leads),
        'checks_not_run': [
            'float', 'negative_float', 'critical_path_length_index', 'baseline_execution',
            'high_duration', 'invalid_dates',
        ],
        'note': (
            'Partial. The date-dependent DCMA checks are not run because scheduling (CPM dates) '
            'is not yet computed; they are listed in checks_not_run rather than reported as '
            'passing.'
        ),
    }


def _statutory_pathway(
    stage_reasonings: Sequence[StageReasoning], city: Optional[str]
) -> List[Dict[str, Any]]:
    """The gates the reasoning actually attached, resolved against the city pathway library."""
    selected = {g.gate_id: g for r in stage_reasonings for g in r.gates}
    if not selected or not city:
        return []
    slug = city.strip().lower().replace(' ', '_').replace('-', '_')
    if slug not in available_cities():
        return []

    out = []
    for entry in load_city_pathway(slug)['entries']:
        if entry['id'] not in selected:
            continue
        selection = selected[entry['id']]
        out.append({
            'id': entry['id'],
            'approval': entry.get('approval'),
            'authority': entry.get('authority'),
            'gates_stage': entry.get('gates_stage'),
            'typical_weeks': entry.get('typical_weeks'),
            'blocks': entry.get('blocks', []),
            'hard_constraint': entry.get('hard_constraint', ''),
            'why': selection.why,
            'confidence': selection.effective_confidence,
            'unverified_dependencies': selection.unverified_dependencies,
            'provenance': entry.get('provenance', {}),
            'compliance_approved': False,
        })
    return out


def _long_lead_register(stage_reasonings: Sequence[StageReasoning]) -> List[Dict[str, Any]]:
    """Long-lead items the reasoning selected, with their library lead times and provenance."""
    selected = {l.lead_id: l for r in stage_reasonings for l in r.long_lead}
    if not selected:
        return []
    out = []
    for entry in load_library('equipment_lead_times')['entries']:
        if entry['id'] not in selected:
            continue
        selection = selected[entry['id']]
        out.append({
            'id': entry['id'],
            'equipment': entry.get('equipment'),
            'typical_weeks': entry.get('typical_weeks'),
            'range_weeks': entry.get('range_weeks'),
            'drives_rfs': entry.get('drives_rfs', False),
            'why': selection.why,
            'confidence': selection.effective_confidence,
            'unverified_dependencies': selection.unverified_dependencies,
            'provenance': entry.get('provenance', {}),
        })
    return out


def build_simulation_output(
    *,
    brief: Dict[str, Any],
    assembly: AssemblyResult,
    stage_reasonings: Sequence[StageReasoning],
    resolved_decisions: Optional[Dict[str, Dict[str, Any]]] = None,
    pending_decisions: Optional[Dict[str, Dict[str, Any]]] = None,
    questions: Optional[Iterable[str]] = None,
    run_id: str = '',
    completed_stages: Optional[Sequence[str]] = None,
) -> SimulationOutput:
    """Assemble the one output object every downstream view projects from."""
    resolved = dict(resolved_decisions or {})
    pending = dict(pending_decisions or {})
    stage_reasonings = list(stage_reasonings)

    # ---- flow: stages, packages, activities, gates, holds, decisions -------------------
    nodes: List[Dict[str, Any]] = []
    for reasoning in stage_reasonings:
        nodes.append({
            'id': f'stage.{reasoning.stage}', 'kind': 'stage', 'stage': reasoning.stage,
            'label': reasoning.stage.replace('_', ' ').title(), 'dept': None,
            'trail_ref': None, 'zone_id': None, 'parent': None,
        })
        for package in reasoning.packages:
            nodes.append({
                'id': f'package.{reasoning.stage}.{package.fragnet_id}',
                'kind': 'work_package', 'stage': reasoning.stage,
                'label': package.fragnet_id, 'dept': None,
                'trail_ref': None, 'zone_id': None,
                'parent': f'stage.{reasoning.stage}',
                'confidence': package.effective_confidence,
                'unverified_dependencies': package.unverified_dependencies,
            })

    for activity in assembly.activities:
        nodes.append({
            'id': activity.id,
            'kind': NODE_KIND_BY_TYPE.get(activity.type, activity.type),
            'stage': activity.stage,
            'label': activity.name,
            'dept': activity.dept_code or None,
            'trail_ref': activity.trail_ref or None,
            'zone_id': activity.zone_id,
            'parent': (
                f'package.{activity.stage}.{activity.source_fragnet}'
                if activity.source_fragnet else f'stage.{activity.stage}'
            ),
            'wbs_id': activity.wbs_id,
            'duration_days': activity.duration_days,
            'safety_flag': activity.safety_flag,
            'hitl_tier': activity.hitl_tier,
            'blocks_export': activity.blocks_export,
            'confidence': activity.confidence,
            'unverified_dependencies': activity.unverified_dependencies,
            # start/finish are absent until scheduling exists; the spec's flow node carries
            # them, so the keys are present and null rather than silently missing.
            'start': None,
            'finish': None,
        })

    nodes.extend(_decision_nodes(resolved, pending))

    edges = [
        {'from': e.from_id, 'to': e.to_id, 'type': e.type, 'lag': e.lag, 'kind': e.kind,
         'why': e.why}
        for e in assembly.edges
    ]

    # ---- reasoning trail: engine entries plus what each stage recorded ------------------
    trail = [t.model_dump() for t in assembly.trail]
    seen = {t['ref_id'] for t in trail}
    for reasoning in stage_reasonings:
        for entry in reasoning.trail:
            if entry.ref_id not in seen:
                trail.append(entry.model_dump())
                seen.add(entry.ref_id)
    for decision_id, payload in sorted(resolved.items()):
        trail.append({
            'ref_id': f'decision.{decision_id}', 'stage': payload.get('stage', ''),
            'decision': payload.get('answer'),
            'why': f'Answered by {payload.get("answered_by", "planner")}: '
                   f'{payload.get("question", decision_id)}',
            'sources': [decision_id], 'confidence': 1.0,
            'decided_by': payload.get('answered_by', 'planner'), 'hitl_tier': 'tier_1',
            'unverified_dependencies': [], 'stated_confidence': None,
        })
    trail.sort(key=lambda t: t['ref_id'])

    flags = [f.model_dump() for f in assembly.flags]
    flags.extend({'kind': 'engine_warning', 'message': w, 'refs': [], 'hitl_tier': 'tier_2'}
                 for w in assembly.warnings)
    for reasoning in stage_reasonings:
        flags.extend({'kind': 'reasoning_warning', 'message': w, 'refs': [reasoning.stage],
                      'hitl_tier': 'tier_2'} for w in reasoning.warnings)

    equipment_counts = assembly.zones[0].get('equipment_counts', []) if assembly.zones else []

    return SimulationOutput(
        project_meta={
            'run_id': run_id,
            'project_name': brief.get('project_name'),
            'client': brief.get('client'),
            'city': brief.get('city'),
            'tier': brief.get('tier'),
            'it_load_mw': brief.get('it_load_mw'),
            'redundancy_topology': brief.get('redundancy_topology'),
            'scope': brief.get('scope'),
            'phasing': brief.get('phasing'),
            'target_rfs_date': brief.get('target_rfs_date'),
            'site_context': brief.get('site_context'),
            'delivery_mode_by_discipline': brief.get('delivery_mode_by_discipline', {}),
            'stages_completed': list(completed_stages or []),
            'library_version': assembly.library_version,
            'corpus_version': assembly.corpus_version,
            'prompt_version': assembly.prompt_version,
        },
        questions=sorted(questions or []),
        decisions=[
            Decision(
                id=decision_id,
                question=payload.get('question', ''),
                answer=payload.get('answer', ''),
                impact=payload.get('impact', ''),
            )
            for decision_id, payload in sorted(resolved.items())
        ],
        flow={'nodes': nodes, 'edges': edges},
        statutory_pathway=_statutory_pathway(stage_reasonings, brief.get('city')),
        equipment_counts=equipment_counts,
        long_lead_register=_long_lead_register(stage_reasonings),
        activities=[a.model_dump() for a in assembly.activities],
        commissioning=list(assembly.commissioning),
        zones=list(assembly.zones),
        reasoning_trail=trail,
        quality={
            'dcma_summary': _dcma_summary(assembly),
            # Complete means: no unanswered fork, nothing Tier-1 awaiting sign-off, and no
            # element resting on unverified library data. All three must hold.
            'governance_complete': (
                not pending
                and not assembly.export_blocked
                and not assembly.governance.get('unverified_dependencies')
            ),
            'export_blocked': assembly.export_blocked,
            'export_block_reason': assembly.governance.get('export_block_reason', ''),
            'tier_1_count': assembly.governance.get('tier_1_count', 0),
            'tier_1_ids': assembly.governance.get('tier_1_ids', []),
            'tier_2_count': assembly.governance.get('tier_2_count', 0),
            'unverified_dependencies': assembly.governance.get('unverified_dependencies', []),
            'open_decision_count': len(pending),
        },
        flags=flags,
    )
