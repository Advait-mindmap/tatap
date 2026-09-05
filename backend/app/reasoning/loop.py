"""The per-stage expert reasoning loop (SIMULATION_AND_REASONING.md §3).

    retrieve precedent -> reason (LLM, grounded, schema-constrained) -> screen for decision
    points -> hand packages to the engine -> emit trail entries

Three validations decide whether the model's output is usable, and all three assume the model
may be wrong rather than that it is right:

1. **Closed vocabulary.** Every fragnet/gate/lead/decision-point id must be one we supplied. An
   id we did not offer is an invented activity by another name, and is discarded.
2. **Citation grounding.** Every `sources` entry must be one we supplied. A composed citation is
   a fabricated precedent, which is worse than none, so the element is discarded with it.
3. **Unverified-data disclosure.** Confidence in a conclusion resting on unverified,
   model-generated library data is capped, the dependency is named in the trail, and a Tier-2
   flag is raised. This is the anti-laundering rule: invented placeholder numbers must not
   re-emerge wearing the model's confident voice.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from backend.app.libraries import (
    available_cities,
    library_version,
    load_city_pathway,
    load_library,
)
from backend.app.libraries.provenance import rests_on_estimated_data
from backend.app.llm import get_adapter
from backend.app.reasoning.prompt import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_user_prompt,
    reasoning_schema,
)
from backend.app.reasoning.stages import (
    PROCUREMENT_STAGE,
    STAGE_DEPARTMENT,
    decision_tags_for,
    is_valid_stage,
)
from backend.app.schemas import (
    GateSelection,
    LongLeadSelection,
    PackageSelection,
    RaisedDecisionPoint,
    ReasoningFlag,
    StageReasoning,
    TrailEntry,
)

#: Ceiling on confidence for any conclusion resting on unverified model-generated library data.
#: Deliberately below the default CONF_THRESHOLD (0.7): a conclusion built on invented numbers
#: can never present itself as more than provisional.
UNVERIFIED_CONFIDENCE_CAP = 0.5


def conf_threshold() -> float:
    try:
        return float(os.getenv('CONF_THRESHOLD', '0.7'))
    except ValueError:
        return 0.7


def corpus_version() -> str:
    return os.getenv('CORPUS_VERSION', 'v1')


# ------------------------------------------------------------------ library gathering


def _entry_is_unverified_invention(entry: Dict[str, Any]) -> bool:
    """Does this entry stand in for real data rather than record it?

    Delegates to provenance.rests_on_estimated_data so that adding an origin (as
    INDUSTRY_ESTIMATE was) cannot silently switch the confidence cap off here.
    """
    return rests_on_estimated_data(entry)


def gather_stage_libraries(stage: str, city: Optional[str] = None) -> Dict[str, List[Dict]]:
    """Everything from the libraries that could apply to this stage.

    This is the closed vocabulary the model must choose from. Nothing outside it is selectable.
    """
    fragnets = [f for f in load_library('fragnets')['entries'] if f.get('stage') == stage]

    gates: List[Dict[str, Any]] = []
    if city:
        slug = city.strip().lower().replace(' ', '_').replace('-', '_')
        if slug in available_cities():
            gates = [
                g for g in load_city_pathway(slug)['entries']
                if g.get('gates_stage') == stage
            ]

    lead_entries = load_library('equipment_lead_times')['entries']
    if stage == PROCUREMENT_STAGE:
        # The procurement stage owns the whole long-lead register directly. Everywhere else the
        # register is derived from what a stage's fragnets consume — but procurement has no
        # fragnets of its own yet, so deriving would hand it an empty register, which is exactly
        # the stage where long-lead exposure most needs to be reasoned about
        # (DOMAIN_KNOWLEDGE.md §4: long-lead gear usually drives RFS; front-load it).
        long_lead = list(lead_entries)
    else:
        # Elsewhere: only the items this stage's fragnets actually consume, so the model is not
        # handed the whole register at every stage and invited to attach things arbitrarily.
        linked = {
            link['requires_delivery_of']
            for frag in fragnets
            for link in frag.get('material_links', [])
        }
        long_lead = [e for e in lead_entries if e['id'] in linked]

    tags = decision_tags_for(stage)
    decision_points = [
        d for d in load_library('decision_points')['entries']
        if tags & set(d.get('applies_to_stages', []))
    ]

    return {
        'fragnets': fragnets,
        'gates': gates,
        'long_lead': long_lead,
        'decision_points': decision_points,
    }


def _index(entries: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {e['id']: e for e in entries}


# ------------------------------------------------------------------------- retrieval


def retrieve_for_stage(
    session: Any, stage: str, brief: Dict[str, Any], k: int = 5
) -> Tuple[List[Dict[str, Any]], List[str], bool]:
    """Retrieve corpus precedent for this stage. Returns (hits_as_dicts, warnings, grounded)."""
    if session is None:
        return [], ['No corpus session supplied; reasoning proceeded with no retrieved precedent.'], False

    from backend.app.rag import retrieve

    query = ' '.join(
        str(part) for part in [
            stage.replace('_', ' '),
            brief.get('city'),
            f"Tier {brief.get('tier')}" if brief.get('tier') else '',
            brief.get('redundancy_topology'),
            'data centre construction sequence precedent',
        ] if part
    )
    result = retrieve(session, query, k=k)
    hits = [{**hit.to_source_ref(), 'text': hit.text, 'title': hit.doc_title} for hit in result.hits]
    grounded = bool(result.precedent_hits)
    return hits, list(result.warnings), grounded


# ------------------------------------------------------------------------ validation


def _validate_selection(
    item: Dict[str, Any],
    id_key: str,
    catalogue: Dict[str, Dict[str, Any]],
    allowed_sources: set,
    rejected: List[Dict[str, str]],
) -> Optional[Tuple[str, Dict[str, Any], List[str], float]]:
    """Check one selected element. Returns (id, entry, sources, confidence) or None if rejected."""
    ident = (item.get(id_key) or '').strip()
    if not ident:
        rejected.append({'id': '<missing>', 'kind': id_key, 'reason': 'no identifier given'})
        return None

    entry = catalogue.get(ident)
    if entry is None:
        rejected.append({
            'id': ident,
            'kind': id_key,
            'reason': 'identifier is not in the library for this stage - the model invented it, '
                      'so it was discarded rather than instanced',
        })
        return None

    raw_sources = [s.strip() for s in (item.get('sources') or []) if isinstance(s, str)]
    sources = [s for s in raw_sources if s in allowed_sources]
    fabricated = [s for s in raw_sources if s not in allowed_sources]
    if fabricated:
        rejected.append({
            'id': ident,
            'kind': id_key,
            'reason': f'cited {len(fabricated)} source(s) that were never supplied '
                      f'({", ".join(fabricated[:3])}) - fabricated citation, element discarded',
        })
        return None

    confidence = float(item.get('confidence') or 0.0)
    return ident, entry, sources, confidence


def _apply_unverified_cap(
    entry: Dict[str, Any], confidence: float
) -> Tuple[float, List[str]]:
    """Cap confidence and name the dependency when the entry is unverified invented data."""
    if _entry_is_unverified_invention(entry):
        return min(confidence, UNVERIFIED_CONFIDENCE_CAP), [entry['id']]
    return confidence, []


# ------------------------------------------------------------------------ the loop


def reason_stage(
    stage: str,
    brief: Dict[str, Any],
    *,
    session: Any = None,
    decisions: Optional[List[Dict[str, Any]]] = None,
    adapter: Any = None,
    threshold: Optional[float] = None,
    k: int = 5,
) -> StageReasoning:
    """Run the expert reasoning loop for one stage."""
    if not is_valid_stage(stage):
        raise ValueError(f'Unknown stage {stage!r}.')

    adapter = adapter or get_adapter()
    threshold = conf_threshold() if threshold is None else threshold
    decisions = decisions or []

    libs = gather_stage_libraries(stage, brief.get('city'))
    hits, warnings, grounded = retrieve_for_stage(session, stage, brief, k=k)

    response = adapter.invoke(
        system=SYSTEM_PROMPT,
        user=build_user_prompt(
            stage=stage,
            dept=STAGE_DEPARTMENT.get(stage, ''),
            brief=brief,
            decisions=decisions,
            fragnets=libs['fragnets'],
            gates=libs['gates'],
            long_lead=libs['long_lead'],
            decision_points=libs['decision_points'],
            sources=hits,
            conf_threshold=threshold,
        ),
        schema=reasoning_schema(),
    )

    return build_stage_reasoning(
        response, stage=stage, libs=libs, hits=hits,
        threshold=threshold, warnings=warnings, grounded=grounded, decisions=decisions,
    )


def build_stage_reasoning(
    response: Dict[str, Any],
    *,
    stage: str,
    libs: Dict[str, List[Dict]],
    hits: Sequence[Dict[str, Any]],
    threshold: float,
    warnings: Optional[List[str]] = None,
    grounded: bool = False,
    decisions: Optional[List[Dict[str, Any]]] = None,
) -> StageReasoning:
    """Validate a raw reasoning response into a StageReasoning.

    Separated from the call so every guardrail is testable without a gateway.
    """
    warnings = list(warnings or [])
    answered = {str(d.get('id') or '') for d in (decisions or [])}
    answers_by_id = {str(d.get('id') or ''): str(d.get('answer') or '') for d in (decisions or [])}
    rejected: List[Dict[str, str]] = []
    trail: List[TrailEntry] = []
    flags: List[ReasoningFlag] = []

    frag_idx = _index(libs['fragnets'])
    gate_idx = _index(libs['gates'])
    lead_idx = _index(libs['long_lead'])
    dp_idx = _index(libs['decision_points'])

    source_ids = {h['ref'] for h in hits}
    library_ids = set(frag_idx) | set(gate_idx) | set(lead_idx) | set(dp_idx)
    allowed_sources = source_ids | library_ids

    packages: List[PackageSelection] = []
    gates: List[GateSelection] = []
    long_lead: List[LongLeadSelection] = []
    decision_points: List[RaisedDecisionPoint] = []

    def record_trail(ident: str, why: str, sources: List[str], stated: float,
                     effective: float, unverified: List[str]) -> None:
        trail.append(TrailEntry(
            ref_id=ident,
            stage=stage,
            why=why,
            sources=sources,
            confidence=effective,
            stated_confidence=stated,
            decided_by='llm',
            # Tier-2 = a non-blocking uncertainty to confirm later (§4).
            hitl_tier='tier_2' if unverified else 'tier_3',
            unverified_dependencies=unverified,
        ))
        if unverified:
            flags.append(ReasoningFlag(
                kind='unverified_library_data',
                message=(
                    f'{ident}: reasoning rests on unverified model-generated library data '
                    f'({", ".join(unverified)}). Confidence capped at '
                    f'{UNVERIFIED_CONFIDENCE_CAP} (model stated {stated:.2f}). The conclusion '
                    'is only as sound as placeholder numbers no human has checked.'
                ),
                refs=[ident, *unverified],
                hitl_tier='tier_2',
            ))

    # ---- work packages --------------------------------------------------------------
    for item in response.get('packages') or []:
        checked = _validate_selection(item, 'fragnet_id', frag_idx, allowed_sources, rejected)
        if checked is None:
            continue
        ident, entry, sources, stated = checked
        effective, unverified = _apply_unverified_cap(entry, stated)
        predecessors = [p for p in (item.get('predecessors') or []) if p in frag_idx]

        packages.append(PackageSelection(
            fragnet_id=ident, why=item.get('why', ''), confidence=stated,
            effective_confidence=effective, sources=sources,
            unverified_dependencies=unverified, predecessors=predecessors,
        ))
        record_trail(ident, item.get('why', ''), sources, stated, effective, unverified)

    # ---- gates ----------------------------------------------------------------------
    for item in response.get('gates') or []:
        checked = _validate_selection(item, 'gate_id', gate_idx, allowed_sources, rejected)
        if checked is None:
            continue
        ident, entry, sources, stated = checked
        effective, unverified = _apply_unverified_cap(entry, stated)
        gates.append(GateSelection(
            gate_id=ident, why=item.get('why', ''), confidence=stated,
            effective_confidence=effective, sources=sources,
            unverified_dependencies=unverified,
        ))
        record_trail(ident, item.get('why', ''), sources, stated, effective, unverified)

    # ---- long-lead ------------------------------------------------------------------
    for item in response.get('long_lead') or []:
        checked = _validate_selection(item, 'lead_id', lead_idx, allowed_sources, rejected)
        if checked is None:
            continue
        ident, entry, sources, stated = checked
        effective, unverified = _apply_unverified_cap(entry, stated)
        long_lead.append(LongLeadSelection(
            lead_id=ident, why=item.get('why', ''), confidence=stated,
            effective_confidence=effective, sources=sources,
            unverified_dependencies=unverified,
        ))
        record_trail(ident, item.get('why', ''), sources, stated, effective, unverified)

    # ---- curated decision points ----------------------------------------------------
    raised: set = set()
    for item in response.get('decision_points') or []:
        ident = (item.get('decision_point_id') or '').strip()
        entry = dp_idx.get(ident)
        if entry is None:
            rejected.append({
                'id': ident or '<missing>',
                'kind': 'decision_point_id',
                'reason': 'not in the decision-point library for this stage',
            })
            continue
        if ident in raised:
            continue
        decision_points.append(RaisedDecisionPoint(
            decision_point_id=ident,
            question=entry['question'],
            why_stuck=item.get('why_stuck') or entry['why_stuck'],
            options=list(entry.get('options', [])),
            impact=entry.get('impact', ''),
            blocking=bool(entry.get('blocking', True)),
            detection='curated',
        ))
        raised.add(ident)

    # ---- dynamic decision points ----------------------------------------------------
    # ---- coverage: a stage that instances nothing must SAY SO ------------------------
    #
    # Found by audit: six of the thirteen stages produced no activities and, unlike every other
    # under-specified thing in this file, four of them raised no decision point either. The run
    # walked approvals, enabling, envelope, fire_bms, fit_out and handover, reported them
    # `completed`, and the planner got a thirteen-stage programme with six stages missing and
    # nothing anywhere saying so.
    #
    # That is the one failure mode this product exists to prevent (CLAUDE.md rule 3). Silence is
    # the worst possible answer here: an empty stage is indistinguishable from a stage with no
    # work, and only the reader knows which their project is. So it stops and asks.
    #
    # Deliberately BLOCKING and deliberately not batched with the low-confidence forks below:
    # those share one cause and one answer, whereas "is there any envelope work on this job?" is
    # a different question per stage and only the reader can answer it.
    if not packages:
        dyn_id = f'dyn.no_coverage.{stage}'
        available = len(libs['fragnets'])
        if available == 0:
            why = (
                f'The fragnet library contains no work packages for the {stage} stage at all, so '
                'there is nothing for the engine to instance. This is a gap in the library, not '
                'a finding about your project - the plan will contain no {stage} work whichever '
                'way you answer, and this records which of the two situations it is.'
            ).replace('{stage}', stage)
        else:
            why = (
                f'The library offers {available} work package(s) for the {stage} stage and the '
                'reasoning selected none of them. That may be correct for this project or it may '
                'be a miss, and the difference is not something the reasoner can settle about '
                'itself.'
            )
        decision_points.append(RaisedDecisionPoint(
            decision_point_id=dyn_id,
            question=(
                f'The {stage} stage would produce no activities. Is it out of scope on this '
                'project, or is the plan incomplete without it?'
            ),
            why_stuck=why,
            options=[
                'Out of scope on this project - leave it out of the plan',
                'In scope - record the plan as incomplete until this stage is covered',
            ],
            impact=(
                f'Either way no {stage} activities are instanced - the engine only instances '
                'library data (CLAUDE.md rule 2). What this decides is whether the finished plan '
                'is reported as complete or as knowingly missing a stage.'
            ),
            blocking=True,
            detection='dynamic',
        ))
        raised.add(dyn_id)

        # The warning is recorded either way, and says which way it was answered so the gap is
        # legible in the output rather than only in the decision log.
        answer = answers_by_id.get(dyn_id, '')
        if dyn_id not in answered:
            verdict = 'awaiting the planner\'s answer'
        elif answer.lower().startswith('out of scope'):
            verdict = 'confirmed out of scope by the planner'
        else:
            verdict = 'IN SCOPE and NOT PLANNED - this plan is knowingly incomplete'
        warnings.append(
            f'NO WORK PACKAGES for the {stage} stage '
            f'({available} available in the library, none instanced): {verdict}.'
        )
        flags.append(ReasoningFlag(
            kind='stage_not_covered',
            message=(
                f'{stage}: no activities were instanced. {why} Status: {verdict}.'
            ),
            refs=[stage],
            hitl_tier='tier_2',
        ))

    # §4: confidence below CONF_THRESHOLD raises a decision point rather than guessing. This
    # tests the model's OWN stated confidence, not the unverified-data cap: a capped confidence
    # is a data-quality problem for admin to verify, not a fork for a planner to decide, and
    # conflating them would halt every branch on seed data while asking an unanswerable question.
    low_confidence: List[Tuple[str, float]] = []
    for selection, id_attr in (
        (packages, 'fragnet_id'), (gates, 'gate_id'), (long_lead, 'lead_id')
    ):
        for element in selection:
            if element.confidence >= threshold:
                continue
            ident = getattr(element, id_attr)
            if f'dyn.low_confidence.{ident}' in raised:
                continue
            low_confidence.append((ident, element.confidence))

    if len(low_confidence) == 1:
        # One item: ask about it directly. Batching a single thing only makes the question vaguer.
        ident, confidence = low_confidence[0]
        dyn_id = f'dyn.low_confidence.{ident}'
        decision_points.append(RaisedDecisionPoint(
            decision_point_id=dyn_id,
            question=f'Does {ident} apply to the {stage} stage on this project?',
            why_stuck=(
                f'The reasoner stated confidence {confidence:.2f}, below the '
                f'{threshold:.2f} threshold. It is asked rather than assumed.'
            ),
            options=['Yes, it applies', 'No, exclude it', 'Applies with modification'],
            impact='Determines whether this package is instanced into the plan.',
            blocking=True,
            detection='dynamic',
        ))
        raised.add(dyn_id)

    elif low_confidence:
        # BATCHED. Every one of these asks the same question for the same reason - the reasoner
        # was not confident, because the library entry behind it is an unverified estimate - and
        # a planner answers them the same way. Asked one at a time, a real run stopped fifteen to
        # twenty times to collect one repeated answer, which trains the reader to click through
        # the prompts and defeats the point of stopping at all.
        #
        # This changes only HOW OFTEN the run interrupts. Each item still gets its own trail
        # entry, its own stated and capped confidence and its own Tier-2 flag, recorded by
        # record_trail above and untouched by anything here.
        #
        # The id is scoped to the stage. A single shared id would land in the run's answers map
        # after the first stage, and every later stage's batch would look already-answered and
        # skip silently - the plan would proceed on unverified data without asking.
        dyn_id = f'dyn.low_confidence.batch.{stage}'
        items = sorted(low_confidence)
        listing = '\n'.join(f'  - {ident} (stated {conf:.2f})' for ident, conf in items)
        decision_points.append(RaisedDecisionPoint(
            decision_point_id=dyn_id,
            question=(
                f'{len(items)} selections for the {stage} stage rest on low-confidence '
                'reasoning. Proceed with the estimates, or stop and obtain real data?'
            ),
            why_stuck=(
                f'Each of these was stated below the {threshold:.2f} confidence threshold, so '
                'none is assumed:\n'
                f'{listing}\n'
                'They share one cause - the library entry behind each is an unverified estimate '
                '- so they are asked once rather than one at a time.'
            ),
            options=['Proceed with the estimates', 'Stop and obtain real data'],
            impact=(
                f'Applies to all {len(items)} items. Either way each keeps its own reasoning '
                'trail entry, its capped confidence and its Tier-2 flag; this decides only '
                'whether the plan is built on them now.'
            ),
            blocking=True,
            detection='dynamic',
        ))
        raised.add(dyn_id)
        for ident, _ in items:
            # Recorded as raised so a later pass over the same stage does not re-ask per item.
            raised.add(f'dyn.low_confidence.{ident}')

    # ---- grounding warnings ---------------------------------------------------------
    if not grounded:
        warnings.append(
            'NOT GROUNDED IN REAL EXECUTION: no real-execution precedent was retrieved for this '
            'stage, so the reasoning rests on library data and generic norms rather than how a '
            'delivered project actually ran (DOMAIN_KNOWLEDGE.md §1).'
        )
    if not any(t.sources for t in trail):
        warnings.append('No element cited any source. Nothing here is auditable as precedent.')
    if rejected:
        warnings.append(
            f'{len(rejected)} element(s) were rejected as invented or fabricated; they were '
            'discarded, not instanced.'
        )

    notes = (response.get('notes') or '').strip()
    if notes:
        flags.append(ReasoningFlag(kind='reasoner_note', message=notes, hitl_tier='tier_3'))

    return StageReasoning(
        stage=stage,
        packages=packages,
        gates=gates,
        long_lead=long_lead,
        decision_points=decision_points,
        trail=trail,
        flags=flags,
        warnings=warnings,
        rejected=rejected,
        grounded_in_real_execution=grounded,
        retrieved_source_ids=sorted(source_ids),
        library_version=library_version(),
        corpus_version=corpus_version(),
        prompt_version=PROMPT_VERSION,
    )
