from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class Brief(BaseModel):
    project_name: str
    city: str
    tier: str
    it_load_mw: float
    client: str
    questions: List[str] = Field(default_factory=list)
    raw_text: Optional[str] = None


# --------------------------------------------------------------------------------------------
# Intake (Task 5). PRODUCT_SPEC.md §3.1: extract a structured brief with a citation per field
# and list what it still needs. SIMULATION_AND_REASONING.md §6: the extraction prompt's only
# job is to read the raw brief into the Brief schema with a provenance citation per field and
# questions[] for anything missing.
# --------------------------------------------------------------------------------------------


class RawBrief(BaseModel):
    """Free text pasted at intake, plus any uploaded document text."""

    text: str
    source_ref: str = 'raw_brief'
    attachments: List[str] = Field(default_factory=list)


class FieldProvenance(BaseModel):
    """Where one extracted field came from.

    `quote` must appear verbatim in the source. `grounded` records whether we checked that
    ourselves rather than taking the model's word for it — an ungrounded quote means the model
    fabricated the citation, and the field is discarded rather than trusted.
    """

    field: str
    quote: str
    confidence: float
    source_ref: str = 'raw_brief'
    grounded: bool = False


class IntakeQuestion(BaseModel):
    """Something intake could not extract confidently, so it asks instead of guessing."""

    field: str
    question: str
    why_needed: str
    blocking: bool = True


class ExtractedBrief(BaseModel):
    """The structured brief. Every field is optional: absent means 'ask', never 'assume'."""

    project_name: Optional[str] = None
    client: Optional[str] = None
    city: Optional[str] = None
    site_context: Optional[str] = None
    in_dc_park_or_sez: Optional[bool] = None
    tier: Optional[str] = None
    redundancy_topology: Optional[str] = None
    it_load_mw: Optional[float] = None
    scope: Optional[str] = None
    delivery_mode_by_discipline: Dict[str, str] = Field(default_factory=dict)
    power_position: Optional[str] = None
    target_rfs_date: Optional[str] = None
    phasing: Optional[str] = None
    special_conditions: Optional[str] = None


class IntakeResult(BaseModel):
    """What the intake stage returns: the brief, its citations, and what it still needs."""

    brief: ExtractedBrief
    field_provenance: Dict[str, FieldProvenance] = Field(default_factory=dict)
    questions: List[IntakeQuestion] = Field(default_factory=list)
    unresolved_fields: List[str] = Field(default_factory=list)
    flagged_conflicts: List[str] = Field(default_factory=list)
    extraction_confidence_overall: float = 0.0
    warnings: List[str] = Field(default_factory=list)
    raw_brief_ref: str = 'raw_brief'
    attachments: List[str] = Field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        """True when NOTHING is outstanding — every field was extracted and nothing was asked.

        Distinct from `can_proceed`: a brief can be complete enough to simulate while still
        missing details a human should fill in (a client name does not change the build, a
        tier does). Collapsing the two would let outstanding questions disappear from view.
        """
        return not self.questions

    @property
    def can_proceed(self) -> bool:
        """True when nothing BLOCKING is outstanding, i.e. the simulation can start."""
        return not any(q.blocking for q in self.questions)

    @property
    def blocking_questions(self) -> List[IntakeQuestion]:
        return [q for q in self.questions if q.blocking]


class Decision(BaseModel):
    id: str
    question: str
    answer: str
    impact: str


class FlowNodeSchema(BaseModel):
    id: str
    kind: str
    stage: str
    label: str
    dept: Optional[str] = None
    trail_ref: Optional[str] = None
    zone_id: Optional[str] = None
    start: Optional[str] = None
    finish: Optional[str] = None


class FlowEdgeSchema(BaseModel):
    from_id: str = Field(alias='from')
    to_id: str = Field(alias='to')
    type: str
    lag: int = 0
    kind: str


class SimulationOutput(BaseModel):
    project_meta: Dict[str, Any]
    questions: List[str] = Field(default_factory=list)
    decisions: List[Decision] = Field(default_factory=list)
    # default_factory must be CALLABLE. This was a dict literal, so constructing a
    # SimulationOutput without an explicit `flow` raised TypeError: 'dict' object is not
    # callable. Task 2's test always passed flow explicitly, so it never surfaced.
    flow: Dict[str, List[Dict[str, Any]]] = Field(
        default_factory=lambda: {'nodes': [], 'edges': []}
    )
    statutory_pathway: List[Dict[str, Any]] = Field(default_factory=list)
    equipment_counts: List[Dict[str, Any]] = Field(default_factory=list)
    long_lead_register: List[Dict[str, Any]] = Field(default_factory=list)
    activities: List[Dict[str, Any]] = Field(default_factory=list)
    commissioning: List[Dict[str, Any]] = Field(default_factory=list)
    zones: List[Dict[str, Any]] = Field(default_factory=list)
    #: The 4D timeline. `rfs_day` is the last finish day across the plan - the far end of the
    #: scrubber - and `zone_timeline` says when each zone comes into existence and which stage
    #: it is in on any given day. Day offsets, not dates: see engine/schedule.py.
    rfs_day: int = 0
    zone_timeline: Dict[str, Any] = Field(default_factory=dict)
    stage_timeline: Dict[str, Any] = Field(default_factory=dict)
    reasoning_trail: List[Dict[str, Any]] = Field(default_factory=list)
    quality: Dict[str, Any] = Field(default_factory=dict)
    #: Structured rather than bare strings: a flag carries its kind, refs and HITL tier so
    #: the UI can route it (Tier-2 confirmations differ from reasoner notes).
    flags: List[Dict[str, Any]] = Field(default_factory=list)


# --------------------------------------------------------------------------------------------
# Per-stage expert reasoning (Task 6). SIMULATION_AND_REASONING.md §3 (the loop), §5 (the
# reasoning trail), §6 (the expert prompt). The LLM reasons and selects WITHIN the corpus and
# libraries; it never emits activities, durations, logic or counts — the engine instances those.
# --------------------------------------------------------------------------------------------


class TrailEntry(BaseModel):
    """One reasoning-trail entry, per SIMULATION_AND_REASONING.md §5.

    `sources` cites the real-execution precedent or the standard used. These travel into P6 as
    UDFs and drive hover-to-explain in the UI, so an uncited entry is not auditable.
    """

    ref_id: str
    stage: str
    decision: Optional[str] = None
    why: str
    sources: List[str] = Field(default_factory=list)
    confidence: float
    decided_by: str = 'llm'
    hitl_tier: str = 'tier_3'
    #: Library entries this reasoning rests on that a human has not verified. Non-empty means
    #: the conclusion is only as good as invented placeholder data, and says so.
    unverified_dependencies: List[str] = Field(default_factory=list)
    #: Confidence before the unverified-data cap was applied, kept for audit.
    stated_confidence: Optional[float] = None


class PackageSelection(BaseModel):
    """A work package the reasoner judged applicable. `fragnet_id` MUST exist in the library."""

    fragnet_id: str
    why: str
    confidence: float
    effective_confidence: float
    sources: List[str] = Field(default_factory=list)
    unverified_dependencies: List[str] = Field(default_factory=list)
    predecessors: List[str] = Field(default_factory=list)


class GateSelection(BaseModel):
    """A statutory or quality gate attached to the stage. `gate_id` MUST exist in the library."""

    gate_id: str
    why: str
    confidence: float
    effective_confidence: float
    sources: List[str] = Field(default_factory=list)
    unverified_dependencies: List[str] = Field(default_factory=list)


class LongLeadSelection(BaseModel):
    """A long-lead item tied to this stage. `lead_id` MUST exist in the library."""

    lead_id: str
    why: str
    confidence: float
    effective_confidence: float
    sources: List[str] = Field(default_factory=list)
    unverified_dependencies: List[str] = Field(default_factory=list)


class RaisedDecisionPoint(BaseModel):
    """A genuine fork. The simulator halts this branch and asks (CLAUDE.md rule 3)."""

    decision_point_id: str
    question: str
    why_stuck: str
    options: List[str] = Field(default_factory=list)
    impact: str = ''
    blocking: bool = True
    #: 'curated' from the decision-point library, or 'dynamic' from low confidence / conflict.
    detection: str = 'curated'


class ReasoningFlag(BaseModel):
    """A non-blocking uncertainty, flagged Tier-2 for later confirmation (§4)."""

    kind: str
    message: str
    refs: List[str] = Field(default_factory=list)
    hitl_tier: str = 'tier_2'


class StageReasoning(BaseModel):
    """What the reasoning loop produced for one stage.

    Note what is absent: no activities, no durations, no logic links. Those are the engine's
    to instance from the selected fragnets (CLAUDE.md rule 2).
    """

    stage: str
    packages: List[PackageSelection] = Field(default_factory=list)
    gates: List[GateSelection] = Field(default_factory=list)
    long_lead: List[LongLeadSelection] = Field(default_factory=list)
    decision_points: List[RaisedDecisionPoint] = Field(default_factory=list)
    trail: List[TrailEntry] = Field(default_factory=list)
    flags: List[ReasoningFlag] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    #: What was discarded and why — invented ids, fabricated citations, unknown references.
    rejected: List[Dict[str, str]] = Field(default_factory=list)
    grounded_in_real_execution: bool = False
    retrieved_source_ids: List[str] = Field(default_factory=list)
    library_version: str = ''
    corpus_version: str = ''
    prompt_version: str = ''

    @property
    def is_halted(self) -> bool:
        """True when a genuine fork stopped this branch: it cannot expand until answered."""
        return any(dp.blocking for dp in self.decision_points)

    @property
    def rests_on_unverified_data(self) -> bool:
        return any(t.unverified_dependencies for t in self.trail)


# --------------------------------------------------------------------------------------------
# Deterministic engine (Task 7). CLAUDE.md rule 2: the engine instances activities, logic,
# durations and counts from the libraries. No LLM call happens in this layer.
# --------------------------------------------------------------------------------------------


class AssembledEdge(BaseModel):
    """A dependency. `kind` distinguishes ordinary logic from the cross-stage constraints."""

    from_id: str
    to_id: str
    type: str = 'FS'
    lag: int = 0
    #: 'fragnet' | 'cross_stage_gate' | 'delivery' | 'compliance' | 'hold_point'
    kind: str = 'fragnet'
    why: str = ''


class AssembledActivity(BaseModel):
    """One instanced activity, milestone, gate or hold point.

    Everything the reasoning layer attached travels with it: department, safety tier, the
    unverified dependencies it rests on and the capped confidence. Governance is not allowed to
    be dropped in assembly.
    """

    id: str
    wbs_id: str
    name: str
    #: 'task' | 'milestone' | 'gate' | 'hold_point'
    type: str = 'task'
    duration_days: int = 0
    calendar: str = '6day'
    dept_code: str = ''
    delivery_mode: str = 'unknown'
    stage: str = ''
    zone_id: Optional[str] = None
    predecessors: List[Dict[str, Any]] = Field(default_factory=list)
    hold_points: List[str] = Field(default_factory=list)
    safety_flag: bool = False
    hitl_tier: str = 'tier_3'
    blocks_export: bool = False
    trail_ref: str = ''
    #: Capped confidence carried from the reasoning selection (never the model's raw claim).
    confidence: float = 0.0
    unverified_dependencies: List[str] = Field(default_factory=list)
    source_fragnet: Optional[str] = None
    compliance_gates: List[str] = Field(default_factory=list)
    #: Earliest start/finish in whole days from day 0, from the engine's forward pass
    #: (engine/schedule.py). Day offsets rather than dates: they are exactly as precise as
    #: durations and logic allow, and real calendar dates arrive with the P6 export.
    start_day: int = 0
    finish_day: int = 0


class AssemblyResult(BaseModel):
    """What the engine produced. Pure function of (reasoning, brief, libraries)."""

    activities: List[AssembledActivity] = Field(default_factory=list)
    edges: List[AssembledEdge] = Field(default_factory=list)
    zones: List[Dict[str, Any]] = Field(default_factory=list)
    commissioning: List[Dict[str, Any]] = Field(default_factory=list)
    trail: List[TrailEntry] = Field(default_factory=list)
    #: Last finish day across the plan — "ready for service" on the 4D scrubber's timeline.
    rfs_day: int = 0
    #: Per zone: first_day, last_day and the ordered stage spans within it, so the 4D model can
    #: answer "what exists on day N, and what stage is it in" without recomputing.
    zone_timeline: Dict[str, Any] = Field(default_factory=dict)
    #: Per stage: from_day/to_day. Most activities carry no zone, so this is what tells the 4D
    #: model when a zone whose stage is known but whose work is not zone-tagged comes into being.
    stage_timeline: Dict[str, Any] = Field(default_factory=dict)
    flags: List[ReasoningFlag] = Field(default_factory=list)
    governance: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    library_version: str = ''
    corpus_version: str = ''
    prompt_version: str = ''

    @property
    def export_blocked(self) -> bool:
        """Tier-1 safety blocks export until a human signs off (CLAUDE.md rule 5)."""
        return bool(self.governance.get('export_blocked'))
