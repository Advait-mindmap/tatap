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
    PATHWAY_BLOCK_ALIASES,
    PATHWAY_LODGEMENT_AFTER,
    GateRule,
    cross_stage_gate_id,
    delivery_gate_id,
    material_link_index,
    statutory_id,
)
from backend.app.engine.ids import activity_id, zone_index_of, hold_point_id, trail_ref, wbs_id
from backend.app.engine.schedule import apply_schedule, stage_timeline, zone_timeline
from backend.app.engine.zones import generate_zones
from backend.app.libraries import (
    available_cities,
    library_version,
    load_city_pathway,
    load_library,
)
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
    activity_name: str,
    safety_entries: Sequence[Dict[str, Any]],
    min_overlap: int = 2,
    site_context: str = '',
) -> List[Dict[str, Any]]:
    """Tier-1 safety entries whose activity_pattern matches this activity name.

    Keyword overlap rather than substring, because library patterns are written as descriptions
    ("HV/MV energisation & live electrical testing") not as the activity names they must catch.
    Deterministic: sorted by id.

    `site_context` filters rules that only apply to some sites. A two-word overlap is a loose
    test, and loose tests produce false positives that matter here: on a greenfield project
    "Raised floor and plinth installation to data halls" matched "Work in or next to live data
    halls (brownfield)" on {data, halls} alone, and a routine fit-out activity came out Tier-1
    and export-blocking. A block that fires on the wrong activity trains people to ignore
    blocks, so it is worth more than a cosmetic fix.

    The condition is library DATA (`applies_when_site_context`), not a rule encoded here.
    """
    words = _keywords(activity_name)
    context = (site_context or '').strip().lower()
    matched = []
    for entry in safety_entries:
        required = (entry.get('applies_when_site_context') or '').strip().lower()
        if required and required != context:
            continue
        if len(words & _keywords(entry.get('activity_pattern', ''))) >= min_overlap:
            matched.append(entry)
    return sorted(matched, key=lambda e: e['id'])


def _selection_governance(selection: Any) -> Tuple[float, List[str], str]:
    """Confidence, unverified dependencies and HITL tier carried from a reasoning selection."""
    confidence = float(getattr(selection, 'effective_confidence', 0.0) or 0.0)
    unverified = sorted(getattr(selection, 'unverified_dependencies', []) or [])
    tier = 'tier_2' if unverified else 'tier_3'
    return confidence, unverified, tier


def _zone_named(name, zone):
    """An activity's name, said with the zone it happens in.

    Eight rows all reading "Rack and cabinet installation" is a plan nobody can read; the zone is
    the only thing that distinguishes them, so it belongs in the name and not merely in a field.
    """
    return name if not zone else f'{name} - {zone.get("name") or zone.get("id")}'


def _pair_by_zone(sources, targets):
    """Wire one fragnet logic link across the zone instances of its two ends.

    Three cases, and each is a construction fact rather than a convenience:

    * Both ends per-zone: hall 3's cabling follows hall 3's raised floor, and nothing about hall
      5 is involved. A cartesian join here would be the classic mistake - it would make every
      hall wait for every other, turning eight parallel fit-outs back into one serial one.
    * Predecessor project-wide, successor per-zone: the backbone is installed once and releases
      every hall.
    * Predecessor per-zone, successor project-wide: the system test waits for ALL the halls,
      which is what makes it a system test.
    """
    by_source = {zone_index_of(s): s for s in sources}
    by_target = {zone_index_of(t): t for t in targets}
    pairs = []
    for zone_index, target in sorted(by_target.items()):
        if zone_index in by_source:
            pairs.append((by_source[zone_index], target))
        elif 0 in by_source:
            pairs.append((by_source[0], target))
        else:
            pairs.extend((source, target) for _, source in sorted(by_source.items()))
    return sorted(set(pairs))


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
    # The statutory pathway is per city, so unlike the other libraries it can legitimately be
    # absent: a city with no pathway file yields no approvals rather than an error, and the
    # coverage fork in the reasoning loop is what tells the planner the approvals stage is empty.
    pathway_lib = libraries.get('city_pathway')
    if pathway_lib is None:
        pathway_lib = _city_pathway_entries(brief.get('city'))
    site_context = str(brief.get('site_context') or '')
    fragnet_index = {f['id']: f for f in fragnet_lib}

    # The zones are derived once here and used for three separate things: instancing the
    # zone-bearing fragnets, staging the cross-stage releases hall by hall, and the 4D model.
    # They were previously derived twice at two different points, which was one edit away from
    # the plan and the model disagreeing about how many halls there are.
    zones = generate_zones(brief, tier_lib)
    zones_by_kind = {}
    for zone in zones:
        zones_by_kind.setdefault(str(zone.get('kind') or ''), []).append(zone)

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
    # Activities in a zone-bearing fragnet that are deliberately NOT per zone. The zone fallback
    # must skip them: filing the campus cabling backbone under hall 1 is not a smaller error
    # than filing it nowhere, it is a wrong one, and the 4D model would draw it inside a hall.
    campus_wide: set = set()
    # (fragnet_id, fragnet_activity_id) -> instanced id, for material-link resolution.
    link_target: Dict[Tuple[str, str], List[str]] = {}
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

            # ---- how many times this fragnet repeats ------------------------------------
            #
            # A fragnet describes the work in ONE hall or ONE electrical room. Instancing it
            # once for a campus with eight of them is the single largest source of thinness in
            # the plan: eight halls of fit-out arrive as one 30-day bar, and the 4D model shows
            # every hall completing on the same day because there is only one activity to key
            # off. `zone_kind` in the library says which zone kind the fragnet repeats across.
            zone_kind = str(fragnet.get('zone_kind') or '')
            zone_instances = list(zones_by_kind.get(zone_kind, ())) if zone_kind else []
            if zone_kind and not zone_instances:
                warnings.append(
                    f'{fragnet["id"]} repeats per "{zone_kind}" zone, of which this plan has '
                    'none, so it was instanced once for the whole project. Check the sizing '
                    'rules produced the zones this fragnet expects.'
                )

            def zones_for(spec: Dict[str, Any]) -> List[Tuple[int, Optional[Dict[str, Any]]]]:
                """The (index, zone) pairs one activity spec is repeated across.

                Index 0 means project-wide: instanced once, no zone suffix. Work that genuinely
                happens once must never be multiplied - a campus has one bulk fuel farm however
                many halls it has, and repeating it would invent seven PESO licences.
                """
                if not zone_instances or spec.get('zone_scope') == 'project':
                    return [(0, None)]
                return list(enumerate(zone_instances, start=1))

            activity_index = -1
            for spec in fragnet.get('activities', []) or []:
                for zone_index, zone in zones_for(spec):
                    activity_index += 1
                    ident = activity_id(stage, fragnet['id'], spec['id'], zone_index)
                    safety_matches = match_safety_rules(
                        spec['name'], safety_lib, site_context=site_context
                    )
                    explicit_safety = bool(spec.get('safety_flag'))
                    is_safety = explicit_safety or bool(safety_matches)
                    hitl = spec.get('hitl_tier') or ('tier_1' if safety_matches else tier)
                    holds = holds_by_activity.get(spec['id'], [])

                    activities.append(AssembledActivity(
                        id=ident,
                        wbs_id=wbs_id(stage_idx, stage, package_index, activity_index),
                        name=_zone_named(spec['name'], zone),
                        type='task',
                        duration_days=int(spec.get('duration_days') or 0),
                        calendar=spec.get('calendar') or '6day',
                        dept_code=fragnet.get('dept') or dept,
                        delivery_mode=delivery_mode,
                        stage=stage,
                        zone_id=(zone or {}).get('id'),
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
                    if zone_instances and zone is None:
                        campus_wide.add(ident)
                    link_target.setdefault((fragnet['id'], spec['id']), []).append(ident)
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
                            name=_zone_named(f'HOLD: {hold["name"]}', zone),
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
                pairs = _pair_by_zone(
                    link_target.get((fragnet['id'], link['from']), []),
                    link_target.get((fragnet['id'], link['to']), []),
                )
                for source, target in pairs:
                    edges.append(AssembledEdge(
                        from_id=source, to_id=target,
                        type=link.get('type', 'FS'), lag=int(link.get('lag') or 0),
                        kind='fragnet', why=f'Fragnet logic from {fragnet["id"]}.',
                    ))

    # ---------------------------------------------------------------- cross-stage gates
    zone_counts = {kind: len(members) for kind, members in zones_by_kind.items()}
    gate_activities, gate_edges, gate_warnings = _build_cross_stage_gates(
        ordered, stage_activity_ids, link_target, fragnet_lib, lead_lib,
        by_id={a.id: a for a in activities}, zone_counts=zone_counts,
    )
    activities.extend(gate_activities)
    edges.extend(gate_edges)
    warnings.extend(gate_warnings)

    # ------------------------------------------------------------- statutory approvals
    #
    # After the cross-stage gates, because an approval blocks the activities those gates have
    # already produced ids for, and the block targets are resolved by id.
    stat_activities, stat_edges, stat_warnings, stat_trail = _build_statutory_activities(
        ordered, stage_activity_ids, link_target, pathway_lib
    )
    activities.extend(stat_activities)
    edges.extend(stat_edges)
    warnings.extend(stat_warnings)
    trail.extend(stat_trail)

    # ---------------------------------------------------------------------- projections
    _attach_zones(activities, campus_wide)
    _apply_predecessors(activities, edges)

    activities.sort(key=lambda a: (STAGE_INDEX.get(a.stage, 99), a.wbs_id, a.id))
    edges.sort(key=lambda e: (e.from_id, e.to_id, e.type, e.kind))
    trail.sort(key=lambda t: t.ref_id)

    commissioning = _commissioning_ladder(activities)

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




#: How long before the work it gates an approval is assumed to be lodged. See
#: `_build_statutory_activities` for why this is a constant and what it costs.
STATUTORY_START_DAY = 0


def _city_pathway_entries(city):
    """The statutory pathway for this city, or nothing.

    Slugged the same way output.py does it, so the schedule and the reported pathway can never
    disagree about which city file they read.
    """
    if not city:
        return []
    slug_name = str(city).strip().lower().replace(' ', '_').replace('-', '_')
    if slug_name not in available_cities():
        return []
    return load_city_pathway(slug_name)['entries']


def _build_statutory_activities(
    ordered: Sequence[StageReasoning],
    stage_activity_ids: Dict[str, List[str]],
    link_target: Dict[Tuple[str, str], List[str]],
    pathway_lib: Sequence[Dict[str, Any]],
) -> Tuple[List[AssembledActivity], List[AssembledEdge], List[str], List[TrailEntry]]:
    """Turn the city-pathway approvals the reasoner selected into real activities and edges.

    Before this, the statutory pathway was REPORTED and nothing more: SimulationOutput carried
    each approval's authority, its typical_weeks and the stages it `blocks`, and not one of them
    became a day of schedule or a single edge. A plan could show an Environmental Clearance that
    takes thirty weeks and start excavation in week two, and nothing in the output contradicted
    it.

    The approvals are already versioned, human-verifiable compliance data (CLAUDE.md: compliance
    and city pathways are data, never model output). This reads that data; it does not add to it.

    MODELLING LIMIT, and it is a real one. Each approval is modelled as starting at project start
    and running its typical duration, because the library says how long an approval takes but not
    when it is lodged. So an approval binds only where its duration outlasts the date the work it
    gates would otherwise begin - which is right for the early ones (an EC at thirty weeks does
    hold up excavation) and understates the late ones (CEIG modelled from day zero clears long
    before energisation, whereas a real project lodges it once the installation is testable and
    can genuinely be held up by it). A warning states this on every plan that uses it.
    """
    selection_by_id = {
        g.gate_id: (reasoning.stage, g)
        for reasoning in ordered for g in reasoning.gates
    }
    activities: List[AssembledActivity] = []
    edges: List[AssembledEdge] = []
    warnings: List[str] = []
    trail: List[TrailEntry] = []
    if not selection_by_id:
        return activities, edges, warnings, trail

    entries = [e for e in pathway_lib if e.get('id') in selection_by_id]
    if not entries:
        return activities, edges, warnings, trail

    by_pathway_id = {e['id']: statutory_id(e['id']) for e in entries}

    for entry in entries:
        pathway_id = entry['id']
        ident = by_pathway_id[pathway_id]
        weeks = entry.get('typical_weeks')
        # Approval durations are quoted in WEEKS and applied as CALENDAR days: an authority does
        # not observe the site's six-day calendar.
        duration = int(round(float(weeks) * 7)) if weeks is not None else 0
        if weeks is None:
            warnings.append(
                f'Statutory approval {pathway_id} has no typical_weeks, so it is a zero-duration '
                'milestone and imposes no delay. The plan understates the time this approval '
                'takes.'
            )
        approval = entry.get('approval') or pathway_id
        authority = entry.get('authority') or ''
        activities.append(AssembledActivity(
            id=ident,
            wbs_id=f'00.st.{pathway_id[-3:]}',
            name=f'{approval} - {authority}' if authority else approval,
            type='task' if duration > 0 else 'milestone',
            duration_days=duration,
            calendar='7day',
            dept_code='liaison',
            stage=entry.get('gates_stage') or 'approvals',
            trail_ref=trail_ref(ident),
            # Statutory approvals rest on unverified library durations, and the pathway itself is
            # confirmed with the client's compliance team rather than assumed (DOMAIN_KNOWLEDGE
            # section 5), so they are never tier_3.
            hitl_tier='tier_2',
            compliance_gates=[pathway_id],
            unverified_dependencies=[pathway_id],
        ))

        # Every activity in a plan must be answerable for. An approval that appears in the
        # schedule with no trail entry is a date nobody can trace, which is exactly what the
        # reasoning trail exists to prevent - and the assembly tests caught this omission.
        selected_stage, selection = selection_by_id[pathway_id]
        trail.append(TrailEntry(
            ref_id=ident,
            stage=entry.get('gates_stage') or selected_stage,
            why=(
                getattr(selection, 'why', '') or
                f'{approval} is on the statutory pathway for this city.'
            ),
            sources=[pathway_id],
            confidence=float(getattr(selection, 'effective_confidence', 0.0) or 0.0),
            stated_confidence=float(getattr(selection, 'confidence', 0.0) or 0.0),
            decided_by='engine',
            hitl_tier='tier_2',
            unverified_dependencies=[pathway_id],
        ))

        # When it can be lodged. Without this the approval starts on day zero and a late one
        # can never bind - see PATHWAY_LODGEMENT_AFTER.
        lodgement = PATHWAY_LODGEMENT_AFTER.get(pathway_id)
        if lodgement:
            # Every instance, not the first: a CEIG inspection covers the whole HV
            # installation, so lodging after one of eight electrical rooms would produce a date
            # the inspector would not recognise.
            for after in sorted(link_target.get(lodgement, [])):
                edges.append(AssembledEdge(
                    from_id=after, to_id=ident, type='FS', lag=0, kind='statutory',
                    why=(
                        f'{approval} cannot be applied for before the work it approves exists. '
                        f'Lodged once {lodgement[1]} of {lodgement[0]} is complete; the '
                        'approval duration runs from there.'
                    ),
                ))
            if not link_target.get(lodgement):
                warnings.append(
                    f'{approval} is lodged after {lodgement[1]} of {lodgement[0]}, which this '
                    'plan did not instance, so it reverts to starting at project start and will '
                    'almost certainly not bind. Its duration is in the plan but its risk is not.'
                )

        for token in entry.get('blocks', []) or []:
            targets: List[str] = []
            if token in stage_activity_ids:
                targets.extend(sorted(stage_activity_ids[token]))
            elif token in PATHWAY_BLOCK_ALIASES:
                for alias in PATHWAY_BLOCK_ALIASES[token]:
                    if alias[0] == 'fragnet':
                        targets.extend(link_target.get((alias[1], alias[2]), []))
                    elif alias[0] == 'statutory' and alias[1] in by_pathway_id:
                        targets.append(by_pathway_id[alias[1]])
            if not targets:
                # Silence here is how the whole pathway stayed inert. Say which token found
                # nothing, so an unroutable block target is a visible gap rather than a no-op.
                warnings.append(
                    f'Statutory approval {pathway_id} ({approval}) blocks "{token}", which '
                    'matched no stage, no alias and no instanced activity in this plan, so it '
                    'constrains nothing. Either the stage was not walked or the token needs a '
                    'row in PATHWAY_BLOCK_ALIASES.'
                )
                continue
            for target in targets:
                edges.append(AssembledEdge(
                    from_id=ident, to_id=target, type='FS', lag=0, kind='statutory',
                    why=(
                        f'{approval} ({authority}) must be in hand first. Statutory pathway, '
                        f'held as versioned compliance data ({pathway_id}) and confirmed with '
                        "the client's compliance team, not assumed."
                    ),
                ))

    if activities:
        warnings.append(
            f'{len(activities)} statutory approval(s) are modelled as starting at project start '
            'and running their typical duration. The library records how long an approval takes '
            'but not when it is lodged, so an approval binds only where its duration outlasts '
            'the work it gates. Approvals late in the programme are therefore modelled as '
            'non-binding, which understates their risk.'
        )
    return activities, edges, warnings, trail


def _delivery_gate(ident, lead_id, zone=None):
    """One "delivery to site" milestone, for the whole project or for one zone."""
    where = f' - {zone}' if zone else ''
    return AssembledActivity(
        id=ident, wbs_id=f'00.dl.{lead_id[-3:]}',
        name=f'Delivery to site: {lead_id}{where}', type='milestone', duration_days=0,
        dept_code='procurement', stage=DELIVERY_GATE.producer_stage, zone_id=zone,
        trail_ref=trail_ref(ident), hitl_tier='tier_3',
    )


def _split_consumers_by_zone(consumer_stages, stage_activity_ids, by_id, zone_kind):
    """Consumers of a gate, grouped by which zone of `zone_kind` they belong to.

    Returns (per-zone map, the rest). Grouping is on the activity's own `zone_id` rather than on
    its id suffix: an activity's zone is a fact about the activity, and reading it from the id
    would tie the gate machinery to the way ids happen to be spelled.
    """
    if not zone_kind:
        return {}, []
    prefix = 'zone.' + zone_kind.replace('_', '-') + '.'
    per_zone = {}
    rest = []
    for consumer_stage in consumer_stages:
        for consumer_id in sorted(stage_activity_ids.get(consumer_stage, [])):
            zone = getattr(by_id.get(consumer_id), 'zone_id', None) or ''
            if zone.startswith(prefix):
                per_zone.setdefault(zone, []).append(consumer_id)
            else:
                rest.append(consumer_id)
    return per_zone, rest


def _build_cross_stage_gates(
    ordered: Sequence[StageReasoning],
    stage_activity_ids: Dict[str, List[str]],
    link_target: Dict[Tuple[str, str], List[str]],
    fragnet_lib: Sequence[Dict[str, Any]],
    lead_lib: Sequence[Dict[str, Any]] = (),
    by_id: Optional[Dict[str, AssembledActivity]] = None,
    zone_counts: Optional[Dict[str, int]] = None,
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
    by_id = by_id or {}
    zone_counts = zone_counts or {}

    def emit_gate(ident: str, label: str, stage: str, rule: GateRule,
                  anchored: bool, zone: str = '') -> AssembledActivity:
        # A per-zone gate belongs to the zone it RELEASES, not to the zone of the stage that
        # produced it. Left to the fallback, every hall's weather-tightness milestone was filed
        # under the shell, and the 4D model drew eight releases in the wrong place.
        return AssembledActivity(
            id=ident, wbs_id=f'00.{rule.kind[:2]}.{ident[-3:]}', name=label, type='gate',
            duration_days=0, dept_code=STAGE_DEPARTMENT.get(stage, ''), stage=stage,
            zone_id=zone or None,
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
        # Partial release: hang the milestone off named activities where the rule names them,
        # and fall back to the whole stage when they are not in this plan - a different fragnet
        # may have been selected, and silently gating on nothing would drop the constraint.
        producers: List[str] = []
        if rule.producer_activities:
            producers = sorted({
                instanced
                for pair in rule.producer_activities
                for instanced in link_target.get(pair, [])
            })
            if not producers:
                warnings.append(
                    f'Gate {ident} ({rule.label}) names {len(rule.producer_activities)} '
                    'activities to release from, none of which are in this plan, so it falls '
                    f'back to waiting for all of "{rule.producer_stage}". That is more '
                    'conservative than intended; the named activities belong to a fragnet this '
                    'run did not select.'
                )
        if not producers:
            producers = sorted(stage_activity_ids.get(rule.producer_stage, []))

        # ---- PER-ZONE RELEASE ------------------------------------------------------------
        #
        # Where the consumers were instanced per zone, the release is too: hall 3's fit-out
        # follows hall 3 being weather-tight, not the campus average. One gate per zone, each
        # released a further slice into the cladding, which is what hall-by-hall working means.
        #
        # This replaces an approximation that let ALL the consumer work start once the FIRST
        # zone was clad. That was better than waiting for the last, and still wrong: it had
        # eight halls of fit-out beginning on one day.
        zone_count = zone_counts.get(rule.release_per_zone_kind, 0)
        per_zone_consumers, project_consumers = _split_consumers_by_zone(
            consumers, stage_activity_ids, by_id, rule.release_per_zone_kind,
        )
        if rule.producer_activities and len(per_zone_consumers) > 1:
            activities.extend(
                emit_gate(f'{ident}.z{i:02d}', f'{rule.label} - {zone_id}',
                          rule.producer_stage, rule, bool(producers), zone=zone_id)
                for i, zone_id in enumerate(sorted(per_zone_consumers), start=1)
            )
            total = len(per_zone_consumers)
            for i, zone_id in enumerate(sorted(per_zone_consumers), start=1):
                zone_gate = f'{ident}.z{i:02d}'
                for producer_id in producers:
                    duration = int(getattr(by_id.get(producer_id), 'duration_days', 0) or 0)
                    # Zone i is done i/N of the way through, so the lead grows per zone. ceil,
                    # so no zone is ever modelled as released instantaneously.
                    lead = -(-duration * i // total) if duration else 0
                    edges.append(AssembledEdge(
                        from_id=producer_id, to_id=zone_gate, type='SS', lag=lead,
                        kind='cross_stage_gate',
                        why=(
                            f'{rule.why} Released for {zone_id}, the {i} of {total} '
                            f'{rule.release_per_zone_kind} zones done at that point '
                            f'({lead} of {duration} days).'
                        ),
                    ))
                for consumer_id in sorted(per_zone_consumers[zone_id]):
                    edges.append(AssembledEdge(
                        from_id=zone_gate, to_id=consumer_id, type='FS', lag=0,
                        kind='cross_stage_gate', why=rule.why,
                    ))
            # Work that was NOT instanced per zone waits for the last zone. A system-wide test
            # is not released by one hall being ready, and releasing it early is the error that
            # would actually mislead a planner.
            last_gate = f'{ident}.z{len(per_zone_consumers):02d}'
            for consumer_id in sorted(project_consumers):
                edges.append(AssembledEdge(
                    from_id=last_gate, to_id=consumer_id, type='FS', lag=0,
                    kind='cross_stage_gate',
                    why=(
                        f'{rule.why} This work is not instanced per zone, so it waits for the '
                        f'last of {len(per_zone_consumers)} zones rather than the first.'
                    ),
                ))
            continue

        activities.append(emit_gate(ident, rule.label, rule.producer_stage, rule, bool(producers)))

        staged = bool(rule.producer_activities) and zone_count > 1
        if rule.release_per_zone_kind and zone_count <= 1:
            warnings.append(
                f'Gate {ident} ({rule.label}) stages its release across '
                f'"{rule.release_per_zone_kind}" zones, of which this plan has {zone_count}. '
                'With one zone or none there is nothing to stage, so it waits for the whole '
                'activity - correct for a single-hall build, and worth knowing.'
            )

        for producer_id in producers:
            if staged:
                producer = by_id.get(producer_id)
                duration = int(getattr(producer, 'duration_days', 0) or 0)
                # ceil, so a release is never modelled as instantaneous.
                lead = -(-duration // zone_count) if duration else 0
                edges.append(AssembledEdge(
                    from_id=producer_id, to_id=ident, type='SS', lag=lead,
                    kind='cross_stage_gate',
                    why=(
                        f'{rule.why} Released after the first of {zone_count} '
                        f'{rule.release_per_zone_kind} zones ({lead} of {duration} days), '
                        'rather than after the last.'
                    ),
                ))
            else:
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
    # Weeks between consecutive units of the same item arriving. Only some items declare one;
    # without it an item keeps a single arrival gating every consumer, which is the old
    # behaviour and the conservative one.
    stagger_days = {
        entry['id']: int(round(float(entry['delivery_stagger_weeks']) * 7))
        for entry in lead_lib
        if entry.get('id') and entry.get('delivery_stagger_weeks') is not None
    }
    links = material_link_index(fragnet_lib)
    for lead_id, pairs in links.items():
        targets = sorted({
            instanced
            for frag_id, act_id in pairs
            for instanced in link_target.get((frag_id, act_id), [])
        })
        if not targets:
            continue

        # ---- STAGED ARRIVAL ---------------------------------------------------------------
        #
        # Eight electrical rooms need eight transformers, and a factory ships them in sequence.
        # Gating all eight rooms behind one arrival milestone said the whole batch lands on one
        # day, which is why every room's transformer placement started together. Where the
        # library declares an interval, each zone gets its own arrival.
        by_zone = {}
        for target in targets:
            zone = getattr(by_id.get(target), 'zone_id', None)
            if zone:
                by_zone.setdefault(zone, []).append(target)
        interval = stagger_days.get(lead_id)
        if interval and len(by_zone) > 1 and len(by_zone) == len(targets):
            activities.extend(
                _delivery_gate(f'{delivery_gate_id(lead_id)}.z{i:02d}', lead_id, zone)
                for i, zone in enumerate(sorted(by_zone), start=1)
            )
            for i, zone in enumerate(sorted(by_zone), start=1):
                zone_gate = f'{delivery_gate_id(lead_id)}.z{i:02d}'
                offset = (i - 1) * interval
                for producer_id in sorted(stage_activity_ids.get(DELIVERY_GATE.producer_stage, [])):
                    edges.append(AssembledEdge(
                        from_id=producer_id, to_id=zone_gate, type='FS',
                        lag=(lead_time_days.get(lead_id) or 0) + offset, kind='delivery',
                        why=(
                            f'{DELIVERY_GATE.why} Unit {i} of {len(by_zone)} for {zone}, '
                            f'arriving {offset} days after the first at the library\'s '
                            f'{interval // 7}-week interval (an unverified estimate).'
                        ),
                    ))
                for target in sorted(by_zone[zone]):
                    edges.append(AssembledEdge(
                        from_id=zone_gate, to_id=target, type='FS', lag=0, kind='delivery',
                        why=DELIVERY_GATE.why,
                    ))
            warnings.append(
                f'{lead_id} is delivered as {len(by_zone)} units staggered '
                f'{interval // 7} weeks apart. The interval is an industry estimate, not a '
                'quoted delivery schedule: the ORDER of arrival is sound, the spacing is not '
                'verified, and it moves the last zone\'s completion.'
            )
            continue

        ident = delivery_gate_id(lead_id)
        activities.append(_delivery_gate(ident, lead_id))
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


def _attach_zones(
    activities: List[AssembledActivity], campus_wide: Optional[set] = None,
) -> None:
    """Give the 4D model a zone for work that was not instanced per zone.

    This used to run over EVERY activity and hard-write `.01`, silently overwriting real zone ids
    and collapsing a whole campus onto its first hall. It now only fills a gap, so an activity
    instanced for a zone keeps that zone, and the fallback survives for the stages that are not
    zone-bearing.
    """
    campus_wide = campus_wide or set()
    for activity in activities:
        # Already placed, or placed nowhere on purpose.
        if activity.zone_id or activity.id in campus_wide:
            continue
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
