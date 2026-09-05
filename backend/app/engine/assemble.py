"""The deterministic engine: reasoning selections -> instanced activities, logic and dates.

CLAUDE.md rule 2: the LLM reasons and selects; the engine instances. **No LLM call happens in
this module.** Assembly is a pure function of (stage reasoning, brief, libraries) — the same
inputs always produce byte-identical output, which is what makes reproducible mode possible even
though the reasoning step itself is not deterministic (SIMULATION_AND_REASONING.md §8).

Governance is carried, not summarised. Every instanced activity keeps the department that owns
it, the compliance gates that constrain it, the safety holds that precede it, and the Tier-2
unverified dependencies with the capped confidence the reasoning layer assigned. Assembly is the
easiest place for that to quietly fall off, which is why it is asserted in tests.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from backend.app.engine.gates import (
    CROSS_STAGE_GATES,
    DELIVERY_GATE,
    GateRule,
    cross_stage_gate_id,
    delivery_gate_id,
    material_link_index,
)
from backend.app.engine.ids import activity_id, hold_point_id, trail_ref, wbs_id
from backend.app.engine.schedule import apply_schedule, stage_timeline, zone_timeline
from backend.app.engine.zones import generate_zones
from backend.app.libraries import library_version, load_library
from backend.app.reasoning.stages import STAGE_DEPARTMENT, STAGE_INDEX
from backend.app.schemas import (
    AssembledActivity,
    AssembledEdge,
    AssemblyResult,
    ReasoningFlag,
    StageReasoning,
    TrailEntry,
)

#: Zone kind each stage's work lands in, for the 4D build-up.
STAGE_ZONE_KIND = {
    'substructure': 'shell',
    'superstructure': 'shell',
    'envelope': 'shell',
    'mep_power': 'electrical_room',
    'mep_cooling': 'cooling_plant',
    'fire_bms': 'data_hall',
    'fit_out': 'data_hall',
    'commissioning': 'data_hall',
}

#: Discipline each stage's delivery mode is read from, so the brief's per-discipline answers
#: reach the activities they govern (decision point dp.delivery_mode).
STAGE_DISCIPLINE = {
    'substructure': 'civil',
    'superstructure': 'structure',
    'envelope': 'civil',
    'mep_power': 'electrical',
    'mep_cooling': 'mechanical',
    'fire_bms': 'fire',
    'fit_out': 'civil',
    'commissioning': 'mechanical',
}

_STOPWORDS = frozenset({
    'and', 'the', 'of', 'to', 'in', 'on', 'or', 'a', 'an', 'at', 'for', 'with', 'next', 'under',
})


def _keywords(text: str) -> frozenset:
    return frozenset(
        word for word in ''.join(
            c.lower() if c.isalnum() else ' ' for c in (text or '')
        ).split() if word not in _STOPWORDS and len(word) > 2
    )


def match_safety_rules(
    activity_name: str, safety_entries: Sequence[Dict[str, Any]], min_overlap: int = 2
) -> List[Dict[str, Any]]:
    """Tier-1 safety entries whose activity_pattern matches this activity name.

    Keyword overlap rather than substring, because library patterns are written as descriptions
    ("HV/MV energisation & live electrical testing") not as the activity names they must catch.
    Deterministic: sorted by id.
    """
    words = _keywords(activity_name)
    matched = [
        entry for entry in safety_entries
        if len(words & _keywords(entry.get('activity_pattern', ''))) >= min_overlap
    ]
    return sorted(matched, key=lambda e: e['id'])


def _selection_governance(selection: Any) -> Tuple[float, List[str], str]:
    """Confidence, unverified dependencies and HITL tier carried from a reasoning selection."""
    confidence = float(getattr(selection, 'effective_confidence', 0.0) or 0.0)
    unverified = sorted(getattr(selection, 'unverified_dependencies', []) or [])
    tier = 'tier_2' if unverified else 'tier_3'
    return confidence, unverified, tier


def assemble(
    stage_reasonings: Sequence[StageReasoning],
    brief: Dict[str, Any],
    *,
    libraries: Optional[Dict[str, Any]] = None,
) -> AssemblyResult:
    """Instance every selected fragnet into activities, wire the logic, attach governance.

    Pure and deterministic. Stages are processed in canonical order regardless of the order they
    arrive in, so a caller cannot perturb the output by reordering its input.
    """
    libraries = libraries or {}
    fragnet_lib = libraries.get('fragnets') or load_library('fragnets')['entries']
    safety_lib = libraries.get('safety_register') or load_library('safety_register')['entries']
    tier_lib = libraries.get('tier_rules') or load_library('tier_rules')['entries']
    lead_lib = (
        libraries.get('equipment_lead_times')
        or load_library('equipment_lead_times')['entries']
    )
    fragnet_index = {f['id']: f for f in fragnet_lib}

    ordered = sorted(
        stage_reasonings, key=lambda r: (STAGE_INDEX.get(r.stage, 99), r.stage)
    )

    activities: List[AssembledActivity] = []
    edges: List[AssembledEdge] = []
    trail: List[TrailEntry] = []
    flags: List[ReasoningFlag] = []
    warnings: List[str] = []
    # stage -> ids instanced in it, for cross-stage gating and for anchoring gate milestones.
    stage_activity_ids: Dict[str, List[str]] = {}
    # (fragnet_id, fragnet_activity_id) -> instanced id, for material-link resolution.
    link_target: Dict[Tuple[str, str], str] = {}
    delivery_modes = {k.lower(): v for k, v in (brief.get('delivery_mode_by_discipline') or {}).items()}

    for reasoning in ordered:
        stage = reasoning.stage
        stage_idx = STAGE_INDEX.get(stage, 99)
        dept = STAGE_DEPARTMENT.get(stage, '')
        discipline = STAGE_DISCIPLINE.get(stage, '')
        delivery_mode = delivery_modes.get(discipline, 'unknown')
        gate_ids = sorted(g.gate_id for g in reasoning.gates)
        flags.extend(reasoning.flags)

        for package_index, selection in enumerate(
            sorted(reasoning.packages, key=lambda p: p.fragnet_id)
        ):
            fragnet = fragnet_index.get(selection.fragnet_id)
            if fragnet is None:
                warnings.append(
                    f'{selection.fragnet_id} was selected at {stage} but is not in the fragnet '
                    'library; nothing was instanced for it.'
                )
                continue

            confidence, unverified, tier = _selection_governance(selection)
            holds_by_activity: Dict[str, List[Dict[str, Any]]] = {}
            for hold in fragnet.get('hold_points', []) or []:
                holds_by_activity.setdefault(hold.get('after', ''), []).append(hold)

            for activity_index, spec in enumerate(fragnet.get('activities', []) or []):
                ident = activity_id(stage, fragnet['id'], spec['id'])
                safety_matches = match_safety_rules(spec['name'], safety_lib)
                explicit_safety = bool(spec.get('safety_flag'))
                is_safety = explicit_safety or bool(safety_matches)
                hitl = spec.get('hitl_tier') or ('tier_1' if safety_matches else tier)
                holds = holds_by_activity.get(spec['id'], [])

                activities.append(AssembledActivity(
                    id=ident,
                    wbs_id=wbs_id(stage_idx, stage, package_index, activity_index),
                    name=spec['name'],
                    type='task',
                    duration_days=int(spec.get('duration_days') or 0),
                    calendar=spec.get('calendar') or '6day',
                    dept_code=fragnet.get('dept') or dept,
                    delivery_mode=delivery_mode,
                    stage=stage,
                    zone_id=None,
                    predecessors=[],
                    hold_points=sorted(h['name'] for h in holds),
                    safety_flag=is_safety,
                    hitl_tier=hitl,
                    # Tier-1 safety blocks export until signed off (CLAUDE.md rule 5).
                    blocks_export=hitl == 'tier_1',
                    trail_ref=trail_ref(ident),
                    confidence=confidence,
                    unverified_dependencies=unverified,
                    source_fragnet=fragnet['id'],
                    compliance_gates=gate_ids,
                ))
                link_target[(fragnet['id'], spec['id'])] = ident
                stage_activity_ids.setdefault(stage, []).append(ident)

                trail.append(TrailEntry(
                    ref_id=ident,
                    stage=stage,
                    why=(
                        f'Instanced from {fragnet["id"]} ({fragnet.get("name", "")}), selected '
                        f'because: {selection.why}'
                    ),
                    sources=sorted(selection.sources) + [fragnet['id']],
                    confidence=confidence,
                    stated_confidence=selection.confidence,
                    decided_by='engine',
                    hitl_tier=hitl,
                    unverified_dependencies=unverified,
                ))

                for hold in holds:
                    hold_ident = hold_point_id(ident, hold['name'])
                    activities.append(AssembledActivity(
                        id=hold_ident,
                        wbs_id=wbs_id(stage_idx, stage, package_index, activity_index),
                        name=f'HOLD: {hold["name"]}',
                        type='hold_point',
                        duration_days=0,
                        dept_code=hold.get('role') or 'qaqc',
                        stage=stage,
                        predecessors=[{'id': ident, 'type': 'FS', 'lag': 0}],
                        hitl_tier=hitl,
                        blocks_export=hitl == 'tier_1',
                        trail_ref=trail_ref(hold_ident),
                        confidence=confidence,
                        unverified_dependencies=unverified,
                        source_fragnet=fragnet['id'],
                        compliance_gates=gate_ids,
                    ))
                    edges.append(AssembledEdge(
                        from_id=ident, to_id=hold_ident, type='FS', lag=0, kind='hold_point',
                        why=f'Quality hold point owned by {hold.get("role", "qaqc")}.',
                    ))

            for link in sorted(
                fragnet.get('logic', []) or [], key=lambda l: (l['from'], l['to'])
            ):
                source = activity_id(stage, fragnet['id'], link['from'])
                target = activity_id(stage, fragnet['id'], link['to'])
                edges.append(AssembledEdge(
                    from_id=source, to_id=target,
                    type=link.get('type', 'FS'), lag=int(link.get('lag') or 0),
                    kind='fragnet', why=f'Fragnet logic from {fragnet["id"]}.',
                ))

    # ---------------------------------------------------------------- cross-stage gates
    gate_activities, gate_edges, gate_warnings = _build_cross_stage_gates(
        ordered, stage_activity_ids, link_target, fragnet_lib, lead_lib
    )
    activities.extend(gate_activities)
    edges.extend(gate_edges)
    warnings.extend(gate_warnings)

    # ---------------------------------------------------------------------- projections
    _attach_zones(activities)
    _apply_predecessors(activities, edges)

    activities.sort(key=lambda a: (STAGE_INDEX.get(a.stage, 99), a.wbs_id, a.id))
    edges.sort(key=lambda e: (e.from_id, e.to_id, e.type, e.kind))
    trail.sort(key=lambda t: t.ref_id)

    commissioning = _commissioning_ladder(activities)
    zones = generate_zones(brief, tier_lib)

    tier1 = [a for a in activities if a.hitl_tier == 'tier_1']
    tier2 = [a for a in activities if a.hitl_tier == 'tier_2']
    resting_on_estimates = sorted({
        dep for a in activities for dep in a.unverified_dependencies
    })
    if resting_on_estimates:
        warnings.append(
            f'{len(tier2)} assembled activities rest on {len(resting_on_estimates)} unverified '
            'library entries; their confidence is the capped value, not the reasoner\'s claim.'
        )

    # Dates are the ENGINE's to produce (CLAUDE.md rule 2), so the forward pass runs here,
    # after the logic is wired and before anything projects from the result. Doing it in a view
    # would let two views disagree about when the same activity happens.
    rfs_day = apply_schedule(activities)
    timeline = zone_timeline(activities)
    stages_by_day = stage_timeline(activities)

    reference = ordered[0] if ordered else None
    return AssemblyResult(
        activities=activities,
        edges=edges,
        zones=zones,
        rfs_day=rfs_day,
        zone_timeline=timeline,
        stage_timeline=stages_by_day,
        commissioning=commissioning,
        trail=trail,
        flags=flags,
        warnings=warnings,
        governance={
            'tier_1_count': len(tier1),
            'tier_1_ids': sorted(a.id for a in tier1),
            'tier_2_count': len(tier2),
            'export_blocked': bool(tier1),
            'export_block_reason': (
                f'{len(tier1)} Tier-1 safety activities require a named sign-off before export '
                '(CLAUDE.md rule 5, DOMAIN_KNOWLEDGE.md §7).' if tier1 else ''
            ),
            'unverified_dependencies': resting_on_estimates,
            'compliance_gates': sorted({g for a in activities for g in a.compliance_gates}),
        },
        library_version=library_version(),
        corpus_version=getattr(reference, 'corpus_version', '') or '',
        prompt_version=getattr(reference, 'prompt_version', '') or '',
    )


def _build_cross_stage_gates(
    ordered: Sequence[StageReasoning],
    stage_activity_ids: Dict[str, List[str]],
    link_target: Dict[Tuple[str, str], str],
    fragnet_lib: Sequence[Dict[str, Any]],
    lead_lib: Sequence[Dict[str, Any]] = (),
) -> Tuple[List[AssembledActivity], List[AssembledEdge], List[str]]:
    """Emit gate milestones and their edges from the declarative rules.

    A gate is emitted whether or not its producing stage instanced anything. With no producer
    the milestone is unanchored but still gates its consumers, so the constraint is carried
    today and simply acquires a predecessor once frag.design.* / frag.procurement.* exist.
    """
    activities: List[AssembledActivity] = []
    edges: List[AssembledEdge] = []
    warnings: List[str] = []
    stages_present = {r.stage for r in ordered}

    def emit_gate(ident: str, label: str, stage: str, rule: GateRule,
                  anchored: bool) -> AssembledActivity:
        return AssembledActivity(
            id=ident, wbs_id=f'00.{rule.kind[:2]}.{ident[-3:]}', name=label, type='gate',
            duration_days=0, dept_code=STAGE_DEPARTMENT.get(stage, ''), stage=stage,
            trail_ref=trail_ref(ident), hitl_tier='tier_3',
            unverified_dependencies=[] if anchored else [],
        )

    for rule in CROSS_STAGE_GATES:
        consumers = [s for s in rule.consumer_stages if s in stages_present]
        if not consumers:
            continue
        # A gate whose producing stage was never walked is a phantom constraint: nothing can
        # ever satisfy it, and it would hold its consumers behind a milestone representing work
        # nobody planned. An unanchored gate is legitimate only for a stage that WAS walked and
        # instanced nothing - design and procurement before their fragnets existed - which is
        # the case the rule below preserves.
        if rule.producer_stage not in stages_present:
            continue
        ident = cross_stage_gate_id(rule)
        producers = sorted(stage_activity_ids.get(rule.producer_stage, []))
        activities.append(emit_gate(ident, rule.label, rule.producer_stage, rule, bool(producers)))

        for producer_id in producers:
            edges.append(AssembledEdge(
                from_id=producer_id, to_id=ident, type='FS', lag=0,
                kind='cross_stage_gate', why=rule.why,
            ))
        if not producers:
            # Say WHICH of the two reasons it is. They have different remedies: one is a library
            # gap for whoever maintains the libraries, the other is a planning answer about this
            # project. The old message asserted the first unconditionally, which was wrong as
            # soon as a stage could be walked, covered by the library, and still empty because
            # the planner put it out of scope.
            covered = any(f.get('stage') == rule.producer_stage for f in fragnet_lib)
            reason = (
                f'the stage instanced nothing although the library covers it - most likely it '
                f'was answered out of scope on this project'
                if covered else
                f'frag.{rule.producer_stage}.* does not exist yet'
            )
            warnings.append(
                f'Gate {ident} ({rule.label}) has no producing activities: stage '
                f'"{rule.producer_stage}" instanced nothing, because {reason}. The gate still '
                'constrains its consumers, and anchors automatically once that stage produces '
                'work.'
            )

        for consumer_stage in consumers:
            for consumer_id in sorted(stage_activity_ids.get(consumer_stage, [])):
                edges.append(AssembledEdge(
                    from_id=ident, to_id=consumer_id, type='FS', lag=0,
                    kind='cross_stage_gate', why=rule.why,
                ))

    # Delivery gates: one per long-lead item any instanced fragnet declares a link to.
    #
    # Lead times are quoted in WEEKS and applied as CALENDAR days (x7), not working days: a
    # factory building a transformer does not observe the site's 6-day calendar.
    lead_time_days = {
        entry['id']: int(round(float(entry['typical_weeks']) * 7))
        for entry in lead_lib
        if entry.get('id') and entry.get('typical_weeks') is not None
    }
    links = material_link_index(fragnet_lib)
    for lead_id, pairs in links.items():
        targets = sorted({
            link_target[(frag_id, act_id)]
            for frag_id, act_id in pairs
            if (frag_id, act_id) in link_target
        })
        if not targets:
            continue
        ident = delivery_gate_id(lead_id)
        activities.append(AssembledActivity(
            id=ident, wbs_id=f'00.dl.{lead_id[-3:]}',
            name=f'Delivery to site: {lead_id}', type='milestone', duration_days=0,
            dept_code='procurement', stage=DELIVERY_GATE.producer_stage,
            trail_ref=trail_ref(ident), hitl_tier='tier_3',
        ))
        # THE LEAD TIME IS THE LAG. Ordering and arrival are separated by the manufacturing
        # and shipping time the library records; without it the delivery milestone sat on the
        # day the order was placed and a 32-week transformer constrained nothing at all. The
        # gate takes the max over its predecessors, so hanging it off every procurement
        # activity resolves to "PO placed, then wait the lead time".
        lead_days = lead_time_days.get(lead_id)
        for producer_id in sorted(stage_activity_ids.get(DELIVERY_GATE.producer_stage, [])):
            edges.append(AssembledEdge(
                from_id=producer_id, to_id=ident, type='FS', lag=lead_days or 0,
                kind='delivery', why=DELIVERY_GATE.why,
            ))
        if lead_days is None:
            warnings.append(
                f'Delivery gate {ident} has no lead time in equipment_lead_times: it imposes no '
                'delay, so the plan understates the date this plant is available.'
            )
        for target in targets:
            edges.append(AssembledEdge(
                from_id=ident, to_id=target, type='FS', lag=0,
                kind='delivery', why=DELIVERY_GATE.why,
            ))
    return activities, edges, warnings


def _attach_zones(activities: List[AssembledActivity]) -> None:
    for activity in activities:
        kind = STAGE_ZONE_KIND.get(activity.stage)
        if kind:
            activity.zone_id = f'zone.{kind.replace("_", "-")}.01'


def _apply_predecessors(
    activities: List[AssembledActivity], edges: List[AssembledEdge]
) -> None:
    """Fold edges into each activity's predecessors list, sorted for determinism."""
    by_id = {a.id: a for a in activities}
    incoming: Dict[str, List[Dict[str, Any]]] = {}
    for edge in edges:
        if edge.to_id in by_id:
            incoming.setdefault(edge.to_id, []).append(
                {'id': edge.from_id, 'type': edge.type, 'lag': edge.lag, 'kind': edge.kind}
            )
    for ident, preds in incoming.items():
        by_id[ident].predecessors = sorted(
            preds, key=lambda p: (p['id'], p['type'], p['lag'], p['kind'])
        )


def _commissioning_ladder(activities: Iterable[AssembledActivity]) -> List[Dict[str, Any]]:
    """The L1-L5 ladder, in order, with IST marked (DOMAIN_KNOWLEDGE.md §4)."""
    ladder = []
    for activity in activities:
        if activity.stage != 'commissioning' or activity.type != 'task':
            continue
        name = activity.name.lower()
        level = next((lvl for lvl in ('l1', 'l2', 'l3', 'l4', 'l5') if name.startswith(lvl)), '')
        ladder.append({
            'level': level.upper(),
            'name': activity.name,
            'activity_id': activity.id,
            'is_IST': 'integrated systems test' in name,
            'safety_flag': activity.safety_flag,
            'hitl_tier': activity.hitl_tier,
            'blocks_export': activity.blocks_export,
        })
    return sorted(ladder, key=lambda item: (item['level'], item['activity_id']))
