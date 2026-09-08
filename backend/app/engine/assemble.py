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

#: Which review tier outranks which, for rolling governance up from steps to their deliverable.
HITL_SEVERITY = {'tier_3': 0, 'tier_2': 1, 'tier_1': 2}

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

#: Tier-3 discipline -> the discipline a BRIEF states a delivery mode for.
#:
#: The two vocabularies are not the same and should not be forced to be. The brief speaks the
#: language of how work is let - civil, structure, electrical, mechanical, gensets, fire, bms -
#: and the library speaks the language of who does it, which is finer and includes packages
#: nobody lets separately (testing, procurement, compliance, management). A discipline absent
#: here has no stated mode of its own and falls back to its stage's.
BRIEF_DISCIPLINE = {
    'civil': 'civil',
    'structural': 'structure',
    # An architectural package is let with the builder's work on the jobs this library covers.
    'architectural': 'civil',
    'mechanical': 'mechanical',
    'electrical': 'electrical',
    'fire': 'fire',
    'controls': 'bms',
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


def _expand_steps(activities):
    """Flatten a fragnet's activities, replacing any that declare `steps` with those steps.

    Tier 4. A library activity like "Transformer placement and alignment, 8 days" is a
    DELIVERABLE, not a thing a foreman can sequence: it hides a rigging study, an offload, a
    levelling, a grouting and a set of pre-energisation tests, which happen in that order and
    need different trades and different plant on site.

    The parent is REPLACED rather than kept alongside its steps, because keeping both would
    double-count the duration. Its identity survives in `parent`, which is what the WBS groups on
    and what a gate or a statutory lodgement naming the parent still resolves through.

    Returns (flattened specs, {parent id: [step ids in order]}).
    """
    flat = []
    children = {}
    for spec in activities or []:
        steps = spec.get('steps') or []
        if not steps:
            flat.append(dict(spec, parent=None))
            continue
        for step in steps:
            ident = f'{spec["id"]}-{step["id"]}'
            children.setdefault(spec['id'], []).append(ident)
            flat.append({
                **step,
                'id': ident,
                'parent': spec['id'],
                'parent_name': spec['name'],
                # Inherited from the deliverable: a step is done on the same calendar, in the
                # same zone, and under the same review tier as the work it is part of.
                'calendar': step.get('calendar') or spec.get('calendar'),
                'zone_scope': spec.get('zone_scope'),
                'hitl_tier': step.get('hitl_tier') or spec.get('hitl_tier'),
                'safety_flag': step.get('safety_flag', spec.get('safety_flag')),
            })
    return flat, children


#: Which end of a decomposed activity a logic link attaches to.
#:
#: Once an 8-day activity is six steps, "c20 -> c30 finish-to-start" has to mean the LAST step of
#: c20 to the FIRST step of c30. Reading the relationship type is not a nicety: attaching both
#: ends to the first step turns a finish-to-start into a start-to-start and quietly overlaps two
#: activities that must not overlap.
_LINK_END = {
    'FS': ('last', 'first'),
    'SS': ('first', 'first'),
    'FF': ('last', 'last'),
    'SF': ('first', 'last'),
}


#: Canonical package order, read from the library so the vocabulary lives with the data that
#: uses it. The order is the one a schedule is normally reported in - the works first, roughly as
#: they happen, then testing, then the commercial and statutory packages.
DISCIPLINE_ORDER = (
    'civil', 'structural', 'architectural', 'mechanical', 'electrical', 'fire', 'controls',
    'testing', 'procurement', 'compliance', 'management',
)


def _discipline_of(spec, fragnet):
    """The sub-package a leaf belongs to.

    Falls back to the fragnet's department so a library entry written before Tier 3, or added
    without a discipline, still lands in one package rather than in a blank one.
    """
    return str(spec.get('discipline') or fragnet.get('dept') or 'management')


def _discipline_rank(name):
    try:
        return DISCIPLINE_ORDER.index(name)
    except ValueError:
        # Something the canonical list does not know sorts after everything it does, by name,
        # rather than silently taking position zero.
        return len(DISCIPLINE_ORDER)


#: What a stated site context MEANS, in the words briefs and fork answers actually use.
#:
#: Brownfield first, and here the order genuinely matters: "brownfield site, adjacent to an
#: operating facility" contains both ideas and is a live site.
SITE_CONTEXT_WORDS = (
    ('brownfield', (
        'brownfield', 'live hall', 'live data hall', 'live facility', 'operating facility',
        'energised', 'energized',
    )),
    ('greenfield', ('greenfield',)),
)


def canonical_site_context(value) -> str:
    """The canonical site context a stated one means, or the stated one lowercased.

    THE ENGINE ASSUMED A CANONICAL STRING NOTHING GUARANTEES. The safety register gates rules on
    `applies_when_site_context: brownfield` and compared it for equality, while intake asks the
    model for "greenfield | brownfield" and stores whatever comes back, and the site-context fork's
    own options read "Brownfield - inside a live hall". A real export answered exactly that came
    out with all thirty raised-floor activities at tier 2 and no live-hall control anywhere.

    Worse, both protections failed on the same mismatch: the control did not attach, AND the export
    gate that exists to report a missing live-site control compared the same exact string and
    reported nothing missing. No control and no warning.

    An unrecognised value is returned as-is rather than forced to either side. Reading an unclear
    context as greenfield would silently drop the tier-1 rule, which is the failure this exists to
    prevent.
    """
    text = str(value or '').strip().lower()
    if not text:
        return ''
    for context, words in SITE_CONTEXT_WORDS:
        if any(word in text for word in words):
            return context
    return text


def _missing_live_site_controls(site_context, matched_rule_ids, safety_entries):
    """Tier-1 rules this site context requires that nothing in the plan actually carries.

    A rule gated on a site context is a control that APPLIES there. If the plan contains no
    activity carrying it, the plan is not describing a safe job - it is describing a job with the
    control left out, and it must not export looking complete.

    Found the hard way: a real export answered "Brownfield - inside a live hall" and contained no
    concurrent-operations control at all, because `match_safety_rules` needs two shared keywords
    between an activity name and a rule's `activity_pattern`, and these patterns are written as
    descriptions rather than as things activity names resemble. Two of five tier-1 rules match
    nothing under any site context.

    This does not invent the missing control - which activities carry a live-hall permit is a
    question about what is adjacent and energised, not about what an activity is called, and
    guessing it is exactly what the libraries forbid. It reports the absence.
    """
    context = (site_context or '').strip().lower()
    if not context:
        return []
    matched = set(matched_rule_ids or ())
    unsatisfied = []
    for entry in safety_entries:
        if (entry.get('applies_when_site_context') or '').strip().lower() != context:
            continue
        if entry.get('hitl_tier') != 'tier_1':
            continue
        # AN UNCONFIRMED MAPPING DOES NOT CLOSE THE GATE. A rule whose attachment nobody has
        # verified may be attached to one narrow activity while the hazard it describes covers far
        # more - live-hall works on a brownfield campus reach MEP tie-ins, containment and
        # commissioning near live plant, not only the one deliverable a keyword happened to
        # select. Counting that as coverage would be the appearance of safety again, which is the
        # failure this whole gate exists to catch.
        if entry.get('mapping_status') != 'verified':
            unsatisfied.append(entry['id'])
            continue
        if entry['id'] not in matched:
            unsatisfied.append(entry['id'])
    return sorted(unsatisfied)


def _delivery_mode_for(discipline, stage_discipline, delivery_modes):
    """How work of this discipline is let on this project.

    Per DISCIPLINE, not per stage. A fragnet bundles disciplines - fire_bms carries fire
    suppression and BMS, fit-out carries civil and electrical - and a stage can only name one
    delivery discipline. Resolving there meant every BMS activity inherited the fire scope's
    mode: a brief stating BMS self-perform and fire subcontract produced BMS work assigned to a
    subcontract package it is not in. Seven of twelve fragnets carry work the stage cannot
    represent, so this was never specific to BMS - that is only where it was noticed.

    The discipline's own stated mode wins; otherwise the stage's, which is the previous
    behaviour and the right answer for a discipline the brief says nothing about.
    """
    key = BRIEF_DISCIPLINE.get(discipline or '')
    if key and key in delivery_modes:
        return delivery_modes[key] or 'unknown'
    return delivery_modes.get(stage_discipline, 'unknown') or 'unknown'


def _zone_named(name, zone):
    """An activity's name, said with the zone it happens in.

    Eight rows all reading "Rack and cabinet installation" is a plan nobody can read; the zone is
    the only thing that distinguishes them, so it belongs in the name and not merely in a field.
    """
    return name if not zone else f'{name} - {zone.get("name") or zone.get("id")}'


def _pair_by_zone(sources, targets, phased=False):
    """Wire one fragnet logic link across the zone instances of its two ends.

    Three cases, and each is a construction fact rather than a convenience:

    * Both ends per-zone: hall 3's cabling follows hall 3's raised floor, and nothing about hall
      5 is involved. A cartesian join here would be the classic mistake - it would make every
      hall wait for every other, turning eight parallel fit-outs back into one serial one.
    * Predecessor project-wide, successor per-zone: the backbone is installed once and releases
      every hall.
    * Predecessor per-zone, successor project-wide: the campus-wide work waits for ALL the
      halls. For a system test that is exactly right - waiting for everything is what makes it a
      system test.

    `phased` changes that last case, and only that one.

    A campus-wide step sitting BETWEEN per-hall steps - the structured cabling backbone lives
    between hall containment and hall patching - fans in from every hall and then fans back out,
    so every hall's completion is pinned to the LAST hall's containment. Under a single handover
    that is correct and costs nothing, because the halls finish together anyway. Under a phased
    handover it silently destroys the phasing: hall 1 cannot be handed over in Q2 2028 if its
    patching waits on hall 4, and the halls come back within days of each other however far
    apart their releases were set.

    So where the brief states a phased handover, campus-wide work follows the FIRST hall rather
    than the last. THIS IS AN INTERPRETATION, and it is the construction it implies: a phased
    campus installs shared infrastructure to serve the hall being handed over and extends it as
    the others come. It is the reading that makes the client's stated phasing achievable, and it
    is recorded on the edge so a planner can see it was chosen.
    """
    by_source = {zone_index_of(s): s for s in sources}
    by_target = {zone_index_of(t): t for t in targets}
    pairs = []
    for zone_index, target in sorted(by_target.items()):
        if zone_index in by_source:
            pairs.append((by_source[zone_index], target))
        elif 0 in by_source:
            pairs.append((by_source[0], target))
        elif phased and by_source:
            pairs.append((by_source[min(by_source)], target))
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
    site_context = canonical_site_context(brief.get('site_context'))
    #: Which safety rules anything in this plan actually carries. A rule that applies to this site
    #: and matches nothing is a control that is missing, not a control that is satisfied.
    matched_safety_rules: set = set()
    fragnet_index = {f['id']: f for f in fragnet_lib}
    # (fragnet, activity) -> the duration of the whole DELIVERABLE, steps included.
    #
    # A staged release divides a producing activity's span across the zones it is worked in, so
    # it needs the deliverable's length. Reading it off the instanced activity stopped being
    # right the moment deliverables were decomposed: the last step of a 40-day cladding package
    # is a 4-day sealant pass, and staging across that released every hall at once.
    deliverable_days: Dict[Tuple[str, str], int] = {}
    for entry in fragnet_lib:
        for spec in entry.get('activities') or []:
            steps = spec.get('steps') or []
            deliverable_days[(entry['id'], spec['id'])] = (
                sum(int(step.get('duration_days') or 0) for step in steps) if steps
                else int(spec.get('duration_days') or 0)
            )

    # The zones are derived once here and used for three separate things: instancing the
    # zone-bearing fragnets, staging the cross-stage releases hall by hall, and the 4D model.
    # They were previously derived twice at two different points, which was one edit away from
    # the plan and the model disagreeing about how many halls there are.
    # A stated handover interval means the halls are meant to complete apart, which changes how
    # campus-wide work inside a zone-bearing fragnet is wired. See _pair_by_zone.
    phased_handover = bool(brief.get('hall_handover_interval_days'))
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
    # (fragnet_id, activity_id) -> the instanced ids that START and FINISH it. For an activity
    # decomposed into steps the two differ, which is what lets a finish-to-start link attach to
    # the last step and a start-to-start to the first.
    first_of: Dict[Tuple[str, str], List[str]] = {}
    # (fragnet_id, fragnet_activity_id) -> instanced id, for material-link resolution.
    link_target: Dict[Tuple[str, str], List[str]] = {}
    delivery_modes = {k.lower(): v for k, v in (brief.get('delivery_mode_by_discipline') or {}).items()}

    for reasoning in ordered:
        stage = reasoning.stage
        stage_idx = STAGE_INDEX.get(stage, 99)
        dept = STAGE_DEPARTMENT.get(stage, '')
        # The stage's OWN delivery discipline, used only where a leaf has none of its own.
        stage_discipline = STAGE_DISCIPLINE.get(stage, '')
        gate_ids = sorted(g.gate_id for g in reasoning.gates)
        flags.extend(reasoning.flags)

        # ---- the sub-packages this stage splits into ------------------------------------
        #
        # The package index used to be the index of the SELECTION - which fragnet the activity
        # came from - and since a stage carries one fragnet it was always 01. The WBS therefore
        # had a package level that divided nothing. It is now the activity's discipline, so
        # substructure separates into earthworks, steelfixing, concrete and testing the way it
        # is actually let.
        stage_disciplines = sorted(
            {
                _discipline_of(spec, fragnet_index[selection.fragnet_id])
                for selection in reasoning.packages
                if selection.fragnet_id in fragnet_index
                for spec in _expand_steps(
                    fragnet_index[selection.fragnet_id].get('activities'))[0]
            },
            key=lambda name: (_discipline_rank(name), name),
        )
        package_index_of = {name: index for index, name in enumerate(stage_disciplines)}
        # Activity numbers run within a package, not within a fragnet, or two disciplines would
        # share a WBS path.
        activity_counter: Dict[int, int] = {}

        for _, selection in enumerate(
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
            # THE BRIEF DECIDES, NOT THE ENGINE. Some work repeats per zone only when the
            # project is phased: a commissioning ladder is per hall when each hall is handed over
            # on its own date, and one campus ladder when the facility is commissioned as a unit.
            # Both are legitimate delivery models, and forcing either onto the wrong project
            # breaks it - per-hall structure on a single handover invents ceremony, campus-only
            # on a phased build makes the client's staggered RFS dates decorative.
            #
            # Expressed as library data rather than an engine rule, so the next fragnet with the
            # same property needs no code change (CLAUDE.md: no hardcoded domain rules).
            zone_kind = str(fragnet.get('zone_kind') or '')
            if not zone_kind and phased_handover:
                zone_kind = str(fragnet.get('zone_kind_when_phased') or '')
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

            specs, step_children = _expand_steps(fragnet.get('activities'))
            library_specs = {
                (fragnet['id'], a['id']): a for a in fragnet.get('activities') or []
            }
            for spec in specs:
                discipline = _discipline_of(spec, fragnet)
                # ---- HOW THIS WORK IS LET, resolved per leaf ---------------------------------
                #
                # Per LEAF, not per stage. A fragnet bundles disciplines - fire_bms carries fire
                # suppression and BMS, fit-out carries civil and electrical - and a stage can
                # only name one delivery discipline. Resolving there meant every BMS activity
                # inherited the fire scope's mode: a brief stating BMS self-perform and fire
                # subcontract produced BMS work assigned to a subcontract package it is not in.
                #
                # Seven of twelve fragnets carry leaves the stage cannot represent, so this was
                # never specific to BMS - that is only where it was noticed. Worst is fit-out,
                # where nine electrical leaves were being let as civil.
                #
                # The leaf's own discipline wins where the brief states a mode for it; otherwise
                # the stage's, which is the old behaviour and the right fallback for a discipline
                # the brief says nothing about.
                delivery_mode = _delivery_mode_for(
                    discipline, stage_discipline, delivery_modes
                )
                package_index = package_index_of.get(discipline, 0)
                activity_index = activity_counter.get(package_index, -1)
                for zone_index, zone in zones_for(spec):
                    activity_index += 1
                    activity_counter[package_index] = activity_index
                    ident = activity_id(stage, fragnet['id'], spec['id'], zone_index)
                    # MATCH THE DELIVERABLE, NOT THE STEP. The register's patterns were
                    # written against library deliverables - "Integrated systems test under load /
                    # on generator" - and Tier 4 decomposition then REPLACED those deliverables
                    # with their steps. The names the rules were written against stopped existing
                    # as leaves, so matching fell through to whichever step happened to share two
                    # words: the IST-under-load control landed on "Test script and load bank
                    # mobilisation", a step of L4 FUNCTIONAL PERFORMANCE TESTING, and the L5 test
                    # it names got nothing from the rule at all.
                    #
                    # The hazard belongs to the deliverable, so every step it was split into
                    # carries it. Flagging one step would let a planner sign off part of an IST.
                    safety_name = spec['name']
                    if spec.get('parent'):
                        safety_name = (
                            library_specs.get((fragnet['id'], spec['parent']), {}).get('name')
                            or spec['name']
                        )
                    safety_matches = match_safety_rules(
                        safety_name, safety_lib, site_context=site_context
                    )
                    matched_safety_rules.update(rule['id'] for rule in safety_matches)
                    unconfirmed_mapping = any(
                        (rule.get('mapping_status') or 'verified') != 'verified'
                        for rule in safety_matches
                    )
                    explicit_safety = bool(spec.get('safety_flag'))
                    is_safety = explicit_safety or bool(safety_matches)
                    hitl = spec.get('hitl_tier') or ('tier_1' if safety_matches else tier)
                    # A hold declared against the deliverable belongs after its LAST step: a
                    # pre-energisation inspection is not passed halfway through the install.
                    last_step = (step_children.get(spec.get('parent') or '') or [None])[-1]
                    # (hold, the package it belongs in). A hold declared on the DELIVERABLE takes
                    # the deliverable's discipline, not the last step's. "Reinforcement
                    # inspection" is a structural hold; it hangs off the end of the reinforcement
                    # package, whose final step happens to be the cast-in earth pits - electrical
                    # scope - and filing the inspection under electrical would put it where no
                    # steelfixing engineer would look for it.
                    holds = [(h, discipline) for h in holds_by_activity.get(spec['id'], [])]
                    if spec.get('parent') and spec['id'] == last_step:
                        parent_spec = library_specs.get((fragnet['id'], spec['parent']), {})
                        parent_discipline = _discipline_of(parent_spec, fragnet)
                        holds += [
                            (h, parent_discipline)
                            for h in holds_by_activity.get(spec['parent'], [])
                        ]

                    activities.append(AssembledActivity(
                        id=ident,
                        wbs_id=wbs_id(stage_idx, stage, package_index, activity_index),
                        name=_zone_named(
                            f'{spec["parent_name"]}: {spec["name"]}'
                            if spec.get('parent') else spec['name'],
                            zone,
                        ),
                        type='task',
                        duration_days=int(spec.get('duration_days') or 0),
                        calendar=spec.get('calendar') or '6day',
                        dept_code=fragnet.get('dept') or dept,
                        delivery_mode=delivery_mode,
                        stage=stage,
                        zone_id=(zone or {}).get('id'),
                        predecessors=[],
                        hold_points=sorted(h['name'] for h, _ in holds),
                        safety_flag=is_safety,
                        safety_mapping_unconfirmed=unconfirmed_mapping,
                        hitl_tier=hitl,
                        # Tier-1 safety blocks export until signed off (CLAUDE.md rule 5).
                        blocks_export=hitl == 'tier_1',
                        trail_ref=trail_ref(ident),
                        confidence=confidence,
                        unverified_dependencies=unverified,
                        source_fragnet=fragnet['id'],
                        discipline=discipline,
                        parent_activity=spec.get('parent'),
                        compliance_gates=gate_ids,
                    ))
                    if zone_instances and zone is None:
                        campus_wide.add(ident)
                    link_target.setdefault((fragnet['id'], spec['id']), []).append(ident)
                    first_of.setdefault((fragnet['id'], spec['id']), []).append(ident)
                    parent = spec.get('parent')
                    if parent:
                        siblings = step_children.get(parent) or []
                        # The deliverable STARTS when its first step does and FINISHES when its
                        # last does. A gate or a statutory lodgement naming the parent keeps
                        # working, and keeps meaning what it meant.
                        if spec['id'] == siblings[0]:
                            first_of.setdefault((fragnet['id'], parent), []).append(ident)
                        if spec['id'] == siblings[-1]:
                            link_target.setdefault((fragnet['id'], parent), []).append(ident)
                    stage_activity_ids.setdefault(stage, []).append(ident)

                    trail.append(TrailEntry(
                        ref_id=ident,
                        stage=stage,
                        why=(
                            # The NAME leads. The id used to, with the name in parentheses
                            # behind it, which put a variable name at the front of the one
                            # sentence a planner reads to decide whether to trust the plan.
                            f'Instanced from {fragnet.get("name") or fragnet["id"]}, selected '
                            f'because: {selection.why}'
                        ),
                        sources=sorted(selection.sources) + [fragnet['id']],
                        confidence=confidence,
                        stated_confidence=selection.confidence,
                        decided_by='engine',
                        hitl_tier=hitl,
                        unverified_dependencies=unverified,
                    ))

                    for hold, hold_discipline in holds:
                        hold_ident = hold_point_id(ident, hold['name'])
                        activities.append(AssembledActivity(
                            id=hold_ident,
                            wbs_id=wbs_id(
                                stage_idx, stage,
                                package_index_of.get(hold_discipline, package_index),
                                activity_index,
                            ),
                            name=_zone_named(f'HOLD: {hold["name"]}', zone),
                            type='hold_point',
                            duration_days=0,
                            dept_code=hold.get('role') or 'qaqc',
                            discipline=hold_discipline,
                            # A hold belongs to whoever does the work it holds. It takes its
                            # DELIVERABLE's discipline, so it must take that discipline's mode:
                            # inheriting the current leaf's gave a structural inspection the
                            # turnkey mode of the electrical step it happened to follow.
                            delivery_mode=_delivery_mode_for(
                                hold_discipline, stage_discipline, delivery_modes
                            ),
                            stage=stage,
                            # The hold belongs where its work is. Left to the zone fallback,
                            # every room's pre-energisation inspection was filed in room 01.
                            zone_id=(zone or {}).get('id'),
                            predecessors=[{'id': ident, 'type': 'FS', 'lag': 0}],
                            hitl_tier=hitl,
                            # A hold takes its tier from the work it holds, so it must take that
                            # work's mapping confidence too. Without this a tier-1 hold derived
                            # from an unverified attachment reads in the export as reviewed
                            # coverage, while the activity it hangs off reads as unverified - the
                            # same fact, told two different ways in one file.
                            safety_mapping_unconfirmed=unconfirmed_mapping,
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

            # Steps run in series inside their deliverable. Anything richer - an overlap, a
            # crew constraint - would be a second layer of invention on top of the durations.
            for parent, children in sorted(step_children.items()):
                for earlier, later in zip(children, children[1:]):
                    for source, target in _pair_by_zone(
                        link_target.get((fragnet['id'], earlier), []),
                        first_of.get((fragnet['id'], later), []),
                        phased=phased_handover,
                    ):
                        edges.append(AssembledEdge(
                            from_id=source, to_id=target, type='FS', lag=0, kind='fragnet',
                            why=f'Execution step order within {parent} ({fragnet["id"]}).',
                        ))

            for link in sorted(
                fragnet.get('logic', []) or [], key=lambda l: (l['from'], l['to'])
            ):
                from_end, to_end = _LINK_END.get(link.get('type', 'FS'), ('last', 'first'))
                pairs = _pair_by_zone(
                    (link_target if from_end == 'last' else first_of)
                    .get((fragnet['id'], link['from']), []),
                    (link_target if to_end == 'last' else first_of)
                    .get((fragnet['id'], link['to']), []),
                    phased=phased_handover,
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
        by_id={a.id: a for a in activities}, zone_counts=zone_counts, first_of=first_of,
        deliverable_days=deliverable_days,
        handover_interval_days=brief.get('hall_handover_interval_days'),
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
    , zones_by_kind=zones_by_kind)
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


    tier1 = [a for a in activities if a.hitl_tier == 'tier_1']
    missing_controls = _missing_live_site_controls(site_context, matched_safety_rules, safety_lib)
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

    # AFTER the forward pass. The ladder now reports each rung's span, rolled up from its
    # execution steps, and a span computed before the dates exist is a row of zeroes.
    commissioning = _commissioning_ladder(activities)
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
            'export_blocked': bool(tier1) or bool(missing_controls),
            'export_block_reason': ' '.join(part for part in (
                (
                    f'{len(tier1)} Tier-1 safety activities require a named sign-off before '
                    'export (CLAUDE.md rule 5, DOMAIN_KNOWLEDGE.md §7).' if tier1 else ''
                ),
                (
                    # Absence, said out loud. A control that applies to this site and appears
                    # nowhere in the plan is the condition the export gate exists for: the file
                    # would otherwise read as a complete programme for a live site while carrying
                    # none of the controls a live site requires. It releases the same way any
                    # Tier-1 item does - on a named signature - so the plan can still ship
                    # deliberately, with a record of who accepted it.
                    f'A {site_context} site requires safety controls this plan does not '
                    f'establish: {", ".join(missing_controls)}. Each is either carried by no '
                    'activity at all, or carried only by a mapping nobody has verified - and an '
                    'unverified mapping may cover one narrow activity while the hazard covers '
                    'many. The plan is not complete for this site context; releasing it needs a '
                    'named sign-off (CLAUDE.md rule 5).' if missing_controls else ''
                ),
            ) if part),
            'missing_site_controls': missing_controls,
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


#: How many instances a statutory approval has. `campus` is the default and claims the least.
#:
#: WHICH approvals are issued per building is a regulatory question the library answers per entry,
#: with a compliance sign-off recorded in provenance. The engine only honours the declaration. An
#: audit asked why CEIG looked hall-specific while the Occupancy Certificate did not; the answer
#: was that neither was - one wore a label from the 4D fallback - and the library had no field in
#: which to say either way, so the question could not even be recorded.
STATUTORY_SCOPES = ('campus', 'per_zone')

#: What a per-zone approval repeats across. "Per building" means per data hall here.
STATUTORY_ZONE_KIND = 'data_hall'


def _build_statutory_activities(
    ordered: Sequence[StageReasoning],
    stage_activity_ids: Dict[str, List[str]],
    link_target: Dict[Tuple[str, str], List[str]],
    pathway_lib: Sequence[Dict[str, Any]],
    zones_by_kind: Optional[Dict[str, List[Dict[str, Any]]]] = None,
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

    hall_zones = list((zones_by_kind or {}).get(STATUTORY_ZONE_KIND, ()))

    for entry in entries:
        pathway_id = entry['id']
        base_ident = by_pathway_id[pathway_id]
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
        base_name = f'{approval} - {authority}' if authority else approval

        # AN UNRECOGNISED SCOPE CLAIMS LESS, NOT MORE. A typo must not invent four Occupancy
        # Certificates; one campus approval is wrong in the safer direction, and the warning says
        # the declaration was not understood rather than swallowing it.
        scope = str(entry.get('scope') or 'campus').strip().lower()
        if scope not in STATUTORY_SCOPES:
            warnings.append(
                f'Statutory approval {pathway_id} declares scope "{entry.get("scope")}", which is '
                f'not one of {", ".join(STATUTORY_SCOPES)}. It is instanced once for the campus. '
                'A per-building approval declared this way is being under-counted.'
            )
            scope = 'campus'
        instances = (
            list(enumerate(hall_zones, start=1))
            if scope == 'per_zone' and hall_zones
            else [(0, None)]
        )

        selected_stage, selection = selection_by_id[pathway_id]
        lodgement = PATHWAY_LODGEMENT_AFTER.get(pathway_id)

        for zone_index, zone in instances:
            first_instance = zone_index <= 1
            ident = base_ident if zone is None else f'{base_ident}.z{zone_index:02d}'

            activities.append(AssembledActivity(
                id=ident,
                wbs_id=f'00.st.{pathway_id[-3:]}',
                name=base_name if zone is None else _zone_named(base_name, zone),
                type='task' if duration > 0 else 'milestone',
                duration_days=duration,
                calendar='7day',
                dept_code='liaison',
                stage=entry.get('gates_stage') or 'approvals',
                # A per-zone approval is genuinely IN its zone - unlike the 4D fallback, which
                # marks what it places. This one is a fact about the certificate.
                zone_id=(zone or {}).get('id'),
                trail_ref=trail_ref(ident),
                # Statutory approvals rest on unverified library durations, and the pathway itself
                # is confirmed with the client's compliance team rather than assumed
                # (DOMAIN_KNOWLEDGE section 5), so they are never tier_3.
                hitl_tier='tier_2',
                compliance_gates=[pathway_id],
                unverified_dependencies=[pathway_id],
            ))

            # Every activity in a plan must be answerable for. An approval that appears in the
            # schedule with no trail entry is a date nobody can trace, which is exactly what the
            # reasoning trail exists to prevent - and the assembly tests caught this omission.
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
            if lodgement:
                after_all = sorted(link_target.get(lodgement, []))
                # A per-hall certificate is lodged after THAT hall's work. Falling back to all of
                # it where the work is not zoned keeps the constraint rather than dropping it.
                after_here = [a for a in after_all if zone_index_of(a) == zone_index] or after_all
                if zone is None:
                    after_here = after_all
                for after in after_here:
                    edges.append(AssembledEdge(
                        from_id=after, to_id=ident, type='FS', lag=0, kind='statutory',
                        why=(
                            f'{approval} cannot be applied for before the work it approves '
                            f'exists. Lodged once {lodgement[1]} of {lodgement[0]} is complete; '
                            'the approval duration runs from there.'
                        ),
                    ))
                if not after_all and first_instance:
                    warnings.append(
                        f'{approval} is lodged after {lodgement[1]} of {lodgement[0]}, which this '
                        'plan did not instance, so it reverts to starting at project start and '
                        'will almost certainly not bind. Its duration is in the plan but its '
                        'risk is not.'
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
                    if first_instance:
                        warnings.append(
                            f'Statutory approval {pathway_id} ({approval}) blocks "{token}", '
                            'which matched no stage, no alias and no instanced activity in this '
                            'plan, so it constrains nothing. Either the stage was not walked or '
                            'the token needs a row in PATHWAY_BLOCK_ALIASES.'
                        )
                    continue
                # A hall's own certificate gates that hall's work. Campus-wide work, and work
                # this approval cannot be paired to, is gated by every instance - which is the
                # conservative reading: no hall opens until its own certificate is in hand.
                blocked = targets
                if zone is not None:
                    blocked = [t for t in targets if zone_index_of(t) == zone_index] or targets
                for target in blocked:
                    edges.append(AssembledEdge(
                        from_id=ident, to_id=target, type='FS', lag=0, kind='statutory',
                        why=(
                            f'{approval} ({authority}) must be in hand first. Statutory pathway, '
                            f'held as versioned compliance data ({pathway_id}) and confirmed '
                            "with the client's compliance team, not assumed."
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


def _delivery_gate(ident, lead_id, zone=None, anchored=True):
    """One "delivery to site" milestone, for the whole project or for one zone.

    `anchored` false means no procurement activity precedes it, so the lead time is applied to
    nothing and the plant arrives on day zero - the same inversion the cross-stage gates guard
    against, and on the item class that most often drives RFS.
    """
    where = f' - {zone}' if zone else ''
    unanchored = '' if anchored else ' [UNANCHORED - no procurement precedes this]'
    return AssembledActivity(
        id=ident, wbs_id=f'00.dl.{lead_id[-3:]}',
        name=f'Delivery to site: {lead_id}{where}{unanchored}', type='milestone',
        duration_days=0,
        dept_code='procurement', stage=DELIVERY_GATE.producer_stage, zone_id=zone,
        trail_ref=trail_ref(ident),
        hitl_tier='tier_3' if anchored else 'tier_1',
        blocks_export=not anchored,
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
    first_of: Optional[Dict[Tuple[str, str], List[str]]] = None,
    deliverable_days: Optional[Dict[Tuple[str, str], int]] = None,
    handover_interval_days: Optional[int] = None,
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
    # Where an activity was not decomposed its start and finish are the same instance, so
    # falling back to `link_target` keeps every existing caller correct.
    first_of = first_of if first_of is not None else link_target
    deliverable_days = deliverable_days or {}

    def emit_gate(ident: str, label: str, stage: str, rule: GateRule,
                  anchored: bool, zone: str = '') -> AssembledActivity:
        """One cross-stage milestone.

        A per-zone gate belongs to the zone it RELEASES, not to the zone of the stage that
        produced it. Left to the fallback, every hall's weather-tightness milestone was filed
        under the shell, and the 4D model drew eight releases in the wrong place.

        AN UNANCHORED GATE BLOCKS EXPORT. It has no predecessors, so the forward pass puts it on
        day zero, and it then releases everything downstream immediately - a milestone reading
        "Commissioning complete" sitting on day one while handover runs free. That is worse than
        a wrong date: the gate INVERTS the constraint it exists to carry, from "handover waits
        for commissioning" to "handover waits for nothing", and it does so while looking like an
        achieved milestone in the export.

        The unanchored case is legitimate - a stage the planner scoped out, or one whose fragnets
        do not exist yet - which is why the gate is still emitted and still carries its
        constraint. What is NOT legitimate is shipping a plan built on it without anyone
        deciding that was right. So it is Tier-1 and export-blocking, the same treatment
        CLAUDE.md gives Tier-1 safety work, and its name says what it is rather than claiming
        the milestone was met.
        """
        return AssembledActivity(
            id=ident,
            wbs_id=f'00.{rule.kind[:2]}.{ident[-3:]}',
            name=label if anchored else f'{label} [UNANCHORED - nothing produces this]',
            type='gate',
            duration_days=0, dept_code=STAGE_DEPARTMENT.get(stage, ''), stage=stage,
            zone_id=zone or None,
            trail_ref=trail_ref(ident),
            hitl_tier='tier_3' if anchored else 'tier_1',
            blocks_export=not anchored,
            unverified_dependencies=[],
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
        # A staged release hangs off where the producing work STARTS and spans its whole
        # length. Hanging it off the finish and measuring the finishing step is how this
        # regressed into "wait for all the cladding" without any test of the gate table changing.
        staged_producers = sorted({
            instanced
            for pair in rule.producer_activities
            for instanced in first_of.get(pair, [])
        })
        staged_days = {
            instanced: deliverable_days.get(pair, 0)
            for pair in rule.producer_activities
            for instanced in first_of.get(pair, [])
        }
        # PER-ZONE CONSUMERS ARE ENOUGH. This used to require the rule to NAME its producing
        # activities, because the staged release below needs them - and a rule without them fell
        # through to one campus gate however zone-instanced its consumers were. That is what
        # pinned every hall's commissioning to the LAST hall's fit-out: hall 2 waited until day
        # 798 with its own fit-out finished on 618, and four halls commissioned on the same day.
        if len(per_zone_consumers) > 1:
            activities.extend(
                emit_gate(f'{ident}.z{i:02d}', f'{rule.label} - {zone_id}',
                          rule.producer_stage, rule, bool(producers), zone=zone_id)
                for i, zone_id in enumerate(sorted(per_zone_consumers), start=1)
            )
            total = len(per_zone_consumers)
            for i, zone_id in enumerate(sorted(per_zone_consumers), start=1):
                zone_gate = f'{ident}.z{i:02d}'
                if rule.producer_activities:
                    for producer_id in staged_producers or producers:
                        duration = staged_days.get(producer_id) or int(
                            getattr(by_id.get(producer_id), 'duration_days', 0) or 0
                        )
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
                else:
                    # NO NAMED PRODUCERS, SO NO STAGED FRACTION TO COMPUTE. The rule releases on
                    # a whole stage, and this hall's gate waits on the work in THIS hall plus any
                    # campus-wide work of that stage. Finish-to-start, because "fit-out complete"
                    # means complete - there is no partial-progress reading of it the way there is
                    # for cladding, where a building becomes weather-tight bay by bay.
                    #
                    # The stagger is inherited rather than imposed: hall 3's fit-out already
                    # starts later because its own release did, so its commissioning follows.
                    # PAIRED BY ZONE INDEX, NOT BY ZONE IDENTITY, so a gate can cross zone
                    # kinds. Hall 2's commissioning needs electrical room 2 energised, not the
                    # whole campus - and room 2 is a different zone from hall 2, so comparing zone
                    # ids finds nothing and falls back to waiting for every room.
                    #
                    # THIS IS AN INTERPRETATION and it is the generator's own structure: zones are
                    # instanced per hall, so electrical-room.02 is the room that serves
                    # data-hall.02. If a project ever pairs rooms to halls differently, this is
                    # the assumption that has to change.
                    own = [pid for pid in producers if zone_index_of(pid) == i]
                    campus = [pid for pid in producers if not zone_index_of(pid)]
                    for producer_id in sorted(set(own) | set(campus)) or sorted(producers):
                        edges.append(AssembledEdge(
                            from_id=producer_id, to_id=zone_gate, type='FS', lag=0,
                            kind='cross_stage_gate',
                            why=(
                                f'{rule.why} Released for {zone_id} by the work in that '
                                f'zone, not by the last of {total} '
                                f'{rule.release_per_zone_kind} zones.'
                            ),
                        ))
                for consumer_id in sorted(per_zone_consumers[zone_id]):
                    edges.append(AssembledEdge(
                        from_id=zone_gate, to_id=consumer_id, type='FS', lag=0,
                        kind='cross_stage_gate', why=rule.why,
                    ))
            # ---- PHASED HANDOVER --------------------------------------------------------
            #
            # Where the brief states that halls hand over at intervals, that interval is a
            # CLIENT COMMITMENT and it dominates. Releasing purely on cladding progress spread
            # seven halls across ten days, which is what a contractor would do if nobody asked
            # otherwise - and the brief had asked otherwise.
            #
            # Expressed as start-to-start from the FIRST hall's gate, so hall i cannot begin
            # before its phased slot. It composes with the weather-tightness release rather than
            # replacing it: a hall waits for whichever comes later, being clad and being due.
            if handover_interval_days and len(per_zone_consumers) > 1:
                first_gate = f'{ident}.z01'
                for i, zone_id in enumerate(sorted(per_zone_consumers), start=2):
                    if i > len(per_zone_consumers):
                        break
                    edges.append(AssembledEdge(
                        from_id=first_gate, to_id=f'{ident}.z{i:02d}', type='SS',
                        lag=(i - 1) * int(handover_interval_days),
                        kind='cross_stage_gate',
                        why=(
                            f'The brief phases hall handover at {handover_interval_days}-day '
                            f'intervals, so this hall is released {(i - 1) * int(handover_interval_days)} '
                            f'days after the first. That interval is an interpretation of the '
                            f'brief\'s wording - see the field provenance for the phrase it '
                            f'came from.'
                        ),
                    ))

            # Work that was NOT instanced per zone waits for the last zone. A system-wide test
            # is not released by one hall being ready, and releasing it early is the error that
            # would actually mislead a planner.
            #
            # UNLESS the brief phases handover. Then the same reading applies here as in
            # _pair_by_zone: a campus-wide step that sits between per-hall work follows the
            # FIRST hall, because a phased campus builds shared infrastructure to serve the hall
            # being handed over. Left on the last hall, the cabling backbone waited for hall 4's
            # cladding and pinned every hall's completion to it - the halls were released six
            # months apart and finished within days of each other, which is the phasing being
            # honoured in the release and thrown away in the result.
            release_index = 1 if handover_interval_days else len(per_zone_consumers)
            last_gate = f'{ident}.z{release_index:02d}'
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
        # `first_of`, not `link_target`: plant is needed when the work STARTS. With an activity
        # decomposed into steps the two differ, and gating the finish would let the offload begin
        # before the transformer had landed.
        targets = sorted({
            instanced
            for frag_id, act_id in pairs
            for instanced in first_of.get((frag_id, act_id), [])
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
            ordering = sorted(stage_activity_ids.get(DELIVERY_GATE.producer_stage, []))
            activities.extend(
                _delivery_gate(f'{delivery_gate_id(lead_id)}.z{i:02d}', lead_id, zone,
                               anchored=bool(ordering))
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
        activities.append(_delivery_gate(
            ident, lead_id,
            anchored=bool(stage_activity_ids.get(DELIVERY_GATE.producer_stage)),
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
            # SAY THAT THIS WAS A GUESS. Without the marker the result is indistinguishable from
            # real zone instancing, and the difference matters to anything that reasons about
            # where work physically happens rather than merely drawing it.
            activity.zone_inferred = True


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
    """The L1-L5 ladder, in order, with IST marked (DOMAIN_KNOWLEDGE.md §4).

    ONE RUNG PER LEVEL, however finely the level is planned. The ladder is a domain artifact - a
    reviewer reads it to see that commissioning climbs L1 to L5 and that the IST is where RFS
    hangs off - not an activity list; there is another one of those.

    That distinction became load-bearing when L3, L4 and L5 were decomposed into execution steps.
    Each step's name begins with its level, so the ladder grew to fourteen rungs with L3 appearing
    four times, which is not a finer ladder but a broken one. Steps are rolled up to the
    deliverable they belong to, and a rolled-up rung spans its steps: it starts when the first
    begins and finishes when the last does.
    """
    rungs: Dict[str, Dict[str, Any]] = {}
    for activity in activities:
        if activity.stage != 'commissioning' or activity.type != 'task':
            continue
        name = activity.name.lower()
        level = next((lvl for lvl in ('l1', 'l2', 'l3', 'l4', 'l5') if name.startswith(lvl)), '')
        # The deliverable is the rung. A step named "L3 ...: static checks" belongs to the L3
        # rung, and its own id is not what a reviewer should be shown.
        key = f'{activity.source_fragnet}:{activity.parent_activity or activity.id}'
        rung = rungs.get(key)
        if rung is None:
            rungs[key] = {
                'level': level.upper(),
                # The deliverable's name, not the step's: everything before the colon.
                'name': activity.name.split(':')[0] if activity.parent_activity
                else activity.name,
                'activity_id': activity.id,
                'is_IST': 'integrated systems test' in name,
                'safety_flag': activity.safety_flag,
                'hitl_tier': activity.hitl_tier,
                'blocks_export': activity.blocks_export,
                'start_day': activity.start_day,
                'finish_day': activity.finish_day,
            }
            continue
        # Governance rolls up conservatively: a rung is as safety-critical, as closely reviewed
        # and as export-blocking as the most demanding step in it.
        rung['is_IST'] = rung['is_IST'] or 'integrated systems test' in name
        rung['safety_flag'] = rung['safety_flag'] or activity.safety_flag
        rung['blocks_export'] = rung['blocks_export'] or activity.blocks_export
        if HITL_SEVERITY.get(activity.hitl_tier, 0) > HITL_SEVERITY.get(rung['hitl_tier'], 0):
            rung['hitl_tier'] = activity.hitl_tier
        rung['start_day'] = min(rung['start_day'], activity.start_day)
        rung['finish_day'] = max(rung['finish_day'], activity.finish_day)
        # The rung is identified by its first step, so a stable id survives re-runs.
        rung['activity_id'] = min(rung['activity_id'], activity.id)
    return sorted(rungs.values(), key=lambda item: (item['level'], item['activity_id']))
