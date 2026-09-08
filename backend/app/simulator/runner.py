"""The simulator: the graph walk, the event stream, and stop-and-ask.

SIMULATION_AND_REASONING.md §2-§4. The simulator walks the stages in execution order, runs the
expert reasoning loop for each, hands the selections to the deterministic engine, and emits the
event stream as it goes.

**Stop-and-ask is the product's differentiator (CLAUDE.md rule 3), so it is modelled as a
resumable state machine rather than as a suspended coroutine.** A generator paused mid-walk dies
with its socket; a run that keeps its state as data survives a dropped connection, a reconnect,
or an answer arriving hours later over a different transport. `Simulator.run()` yields events
until a genuine fork halts it, and returns; the answer is recorded; calling `run()` again resumes
from `completed_stages` with the decisions in hand. Nothing is invented to get past the fork.

This module drives the LLM only through `reason_stage`. The walk itself, the halting, and the
assembly are deterministic.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional, Sequence

from backend.app.engine import assemble
from backend.app.reasoning import reason_stage
from backend.app.reasoning.loop import SITE_CONTEXT_FORK
from backend.app.reasoning.stages import STAGES
from backend.app.schemas import AssemblyResult, SimulationOutput, StageReasoning
from backend.app.simulator.events import (
    ACTIVITY_ADDED,
    DECISION_NEEDED,
    DECISION_RESOLVED,
    GATE_INSERTED,
    PACKAGE_EXPANDED,
    SIMULATION_COMPLETED,
    SIMULATION_ERROR,
    SIMULATION_HALTED,
    SIMULATION_STARTED,
    STAGE_COMPLETED,
    STAGE_STARTED,
    DecisionAnswer,
    RunState,
    SimulationEvent,
)



#: Disciplines a brief can state a delivery mode for. The fork asks about all of them, so a bare
#: answer has to know the full set to tell a gap from a statement.
DELIVERY_DISCIPLINES = (
    'civil', 'structure', 'electrical', 'mechanical', 'gensets', 'fire', 'bms',
)

#: What a planner's words mean, longest idea first so "owner-furnished" is not read as "furnish".
DELIVERY_MODE_WORDS = (
    ('owner-furnished', ('owner-furnish', 'owner furnish', 'owner-suppl', 'free issue', 'ofe')),
    ('self-perform', ('self-perform', 'self perform', 'in-house', 'in house')),
    ('turnkey', ('turnkey',)),
    ('subcontract', ('subcontract', 'sublet')),
)

#: Human labels for the follow-up fork's options.
DELIVERY_MODE_OPTIONS = ('Self-perform', 'Subcontract', 'Turnkey', 'Owner-furnished (OFE)')

#: The answer that promises a per-discipline breakdown.
MIXED_ANSWER = 'mixed'


def read_delivery_mode(answer: str):
    """The mode a planner's answer names, or None if it names none.

    None rather than a guess: an answer that does not name a mode must change nothing. Reading
    "we are still deciding" as a decision would be the same class of invention the libraries
    refuse everywhere else.
    """
    text = (answer or '').strip().lower()
    if not text:
        return None
    for mode, words in DELIVERY_MODE_WORDS:
        if any(word in text for word in words):
            return mode
    return None



#: What a site-context answer means.
#:
#: Brownfield is listed first as a defensive habit, NOT because anything currently depends on it:
#: the two words do not overlap as substrings, so swapping this order changes no behaviour and a
#: mutation proving otherwise stayed alive. It matters only if an option is ever worded so that
#: both words appear ("not greenfield - brownfield adjacent"), and then the safety-relevant
#: reading is the one that must win.
SITE_CONTEXT_WORDS = (
    ('brownfield', ('brownfield', 'live hall', 'live facility', 'energised', 'energized')),
    ('greenfield', ('greenfield',)),
)


def read_site_context(answer: str):
    """The site context an answer names, or None if it names none.

    None rather than a guess: the safety register gates a tier-1 rule on this value, and reading
    an unclear answer as "greenfield" would silently drop that rule - which is the exact fault
    this whole mechanism exists to close.
    """
    text = (answer or '').strip().lower()
    if not text:
        return None
    for context, words in SITE_CONTEXT_WORDS:
        if any(word in text for word in words):
            return context
    return None


class Simulator:
    """Walks the stages, emits events, halts at genuine forks, resumes on an answer."""

    def __init__(
        self,
        brief: Dict[str, Any],
        *,
        run_id: str = 'run-1',
        adapter: Any = None,
        session: Any = None,
        stages: Optional[Sequence[str]] = None,
        libraries: Optional[Dict[str, Any]] = None,
        state: Optional[RunState] = None,
    ) -> None:
        self.brief = brief
        self.adapter = adapter
        self.session = session
        self.stages = list(stages) if stages else list(STAGES)
        self.libraries = libraries
        self.state = state or RunState(run_id=run_id, brief=brief)
        #: Reasoning kept per stage so a resumed run can re-assemble without re-reasoning
        #: stages it already completed.
        self.stage_reasonings: Dict[str, StageReasoning] = {}
        self._emitted_activity_ids: set = set()
        self._emitted_gate_ids: set = set()

    # ------------------------------------------------------------------ event plumbing

    def _event(self, type_: str, stage: str = '', **payload: Any) -> SimulationEvent:
        self.state.seq += 1
        return SimulationEvent(seq=self.state.seq, type=type_, stage=stage, payload=payload)

    # --------------------------------------------------------------------- the walk

    def run(self) -> Iterator[SimulationEvent]:
        """Walk the stages, yielding events. Returns early if a genuine fork halts the run."""
        if not self.state.started:
            self.state.started = True
            yield self._event(
                SIMULATION_STARTED,
                run_id=self.state.run_id,
                stages=self.stages,
                brief_summary={
                    k: self.brief.get(k) for k in ('project_name', 'city', 'tier', 'it_load_mw')
                },
                # The site plan, announced up front so the 3D model can build progressively
                # (VISUALIZATION_SPEC.md section 2). Zones are a deterministic function of load
                # and topology - known the moment the brief is confirmed - whereas the
                # authoritative SimulationOutput only arrives at a settle point. Without this
                # the 3D view sat empty for most of a run and then appeared complete in one
                # jump, which is the opposite of watching a plan get built.
                zones=self._zones(),
            )

        # Any answers received while halted are announced before the walk continues, so a client
        # that reconnects sees why the run is moving again.
        for decision_id, answer in sorted(self.state.answers.items()):
            if decision_id in self.state.pending_decisions:
                self.state.pending_decisions.pop(decision_id, None)
                yield self._event(
                    DECISION_RESOLVED,
                    stage=answer.get('stage', ''),
                    decision_point_id=decision_id,
                    answer=answer.get('answer'),
                    answered_by=answer.get('answered_by', 'planner'),
                )

        for stage in self.stages:
            if stage in self.state.completed_stages:
                continue

            yield self._event(STAGE_STARTED, stage=stage)

            try:
                reasoning = reason_stage(
                    stage,
                    self.brief,
                    session=self.session,
                    decisions=self._resolved_decisions(),
                    adapter=self.adapter,
                )
            except Exception as exc:  # noqa: BLE001 - surfaced to the client, not swallowed
                self.state.halted_at = stage
                yield self._event(
                    SIMULATION_ERROR, stage=stage,
                    error=f'{type(exc).__name__}: {exc}',
                    detail='Reasoning failed for this stage; the walk stopped rather than '
                           'continuing with a gap.',
                )
                return

            self.stage_reasonings[stage] = reasoning

            for package in reasoning.packages:
                yield self._event(
                    PACKAGE_EXPANDED, stage=stage,
                    fragnet_id=package.fragnet_id,
                    why=package.why,
                    confidence=package.effective_confidence,
                    unverified_dependencies=package.unverified_dependencies,
                    sources=package.sources,
                )

            for gate in reasoning.gates:
                if gate.gate_id in self._emitted_gate_ids:
                    continue
                self._emitted_gate_ids.add(gate.gate_id)
                yield self._event(
                    GATE_INSERTED, stage=stage,
                    gate_id=gate.gate_id, why=gate.why,
                    confidence=gate.effective_confidence,
                    unverified_dependencies=gate.unverified_dependencies,
                )

            # ---- stop-and-ask: check BEFORE expanding, so nothing is instanced past a fork
            blocking = [
                dp for dp in reasoning.decision_points
                if dp.blocking and dp.decision_point_id not in self.state.answers
            ]
            if blocking:
                for decision in blocking:
                    self.state.pending_decisions[decision.decision_point_id] = {
                        **decision.model_dump(), 'stage': stage,
                    }
                    yield self._event(
                        DECISION_NEEDED, stage=stage,
                        id=decision.decision_point_id,
                        question=decision.question,
                        why_stuck=decision.why_stuck,
                        options=decision.options,
                        impact=decision.impact,
                        blocking=decision.blocking,
                        detection=decision.detection,
                    )
                self.state.halted_at = stage
                yield self._event(
                    SIMULATION_HALTED, stage=stage,
                    pending=self.state.open_decision_ids,
                    reason='The flow of thought cannot continue without a human decision. '
                           'Nothing downstream is assembled until it is answered.',
                    # The authoritative partial output. A halted run is a legitimate thing to
                    # render - the client should show what has been built so far rather than
                    # holding a half-drawn graph assembled from events alone.
                    output=self.output().model_dump(),
                )
                return

            # ---- expand via the deterministic engine
            result = self._assemble_so_far()
            for activity in result.activities:
                if activity.stage != stage or activity.id in self._emitted_activity_ids:
                    continue
                self._emitted_activity_ids.add(activity.id)
                yield self._event(
                    ACTIVITY_ADDED, stage=stage,
                    id=activity.id, name=activity.name, type=activity.type,
                    wbs_id=activity.wbs_id, duration_days=activity.duration_days,
                    dept_code=activity.dept_code, zone_id=activity.zone_id,
                    predecessors=activity.predecessors,
                    safety_flag=activity.safety_flag, hitl_tier=activity.hitl_tier,
                    blocks_export=activity.blocks_export,
                    confidence=activity.confidence,
                    unverified_dependencies=activity.unverified_dependencies,
                    trail_ref=activity.trail_ref,
                )

            self.state.completed_stages.append(stage)
            self.state.halted_at = None
            yield self._event(
                STAGE_COMPLETED, stage=stage,
                packages=len(reasoning.packages),
                activities=len([a for a in result.activities if a.stage == stage]),
                flags=len(reasoning.flags),
            )

        final = self._assemble_so_far()
        yield self._event(
            SIMULATION_COMPLETED,
            run_id=self.state.run_id,
            stages_completed=list(self.state.completed_stages),
            activity_count=len(final.activities),
            edge_count=len(final.edges),
            zone_count=len(final.zones),
            export_blocked=final.export_blocked,
            governance=final.governance,
            decisions=[
                {'id': k, **v} for k, v in sorted(self.state.answers.items())
            ],
            # The one SimulationOutput every view projects from. The event stream is what makes
            # the draw progressive; this is what makes it CORRECT - a client reconstructing the
            # graph from events alone would drift from the object the backend actually built.
            output=self.output().model_dump(),
        )

    def _zones(self) -> List[Dict[str, Any]]:
        """The zone set this brief implies. Pure, and identical to the one assembly produces."""
        from backend.app.engine.zones import generate_zones
        from backend.app.libraries import load_library

        try:
            tier_lib = (self.libraries or {}).get('tier_rules') or load_library(
                'tier_rules'
            )['entries']
            return generate_zones(self.brief, tier_lib)
        except Exception:  # noqa: BLE001 - a missing site plan must not stop the walk
            return []

    # ------------------------------------------------------------------ stop-and-ask

    def answer(self, answer: DecisionAnswer) -> None:
        """Record a planner's answer. The next `run()` resumes from where the walk halted."""
        if answer.decision_point_id not in self.state.pending_decisions:
            raise KeyError(
                f'{answer.decision_point_id} is not an open decision on this run. '
                f'Open: {sorted(self.state.pending_decisions)}'
            )
        pending = self.state.pending_decisions[answer.decision_point_id]
        self._apply_delivery_mode(answer)
        self._apply_site_context(answer)
        self.state.answers[answer.decision_point_id] = {
            'answer': answer.answer,
            'answered_by': answer.answered_by,
            'note': answer.note,
            'stage': pending.get('stage', ''),
            'question': pending.get('question', ''),
            'impact': pending.get('impact', ''),
            # why_stuck and options are kept AFTER the answer, not discarded with the prompt.
            # VISUALIZATION_SPEC.md section 4 keeps resolved decisions visible so the reasoning
            # stays auditable, and "why this was a fork at all" is the part worth auditing - an
            # answer with no question behind it explains nothing six months later.
            'why_stuck': pending.get('why_stuck', ''),
            'options': list(pending.get('options', []) or []),
        }


    # ------------------------------------------------------------------ delivery mode
    #
    # An answer has to change the plan or the fork is theatre. `dp.delivery_mode` stopped the
    # run, asked which disciplines are self-performed, recorded the reply and did nothing with
    # it: `self.brief` is set once at construction and nothing wrote back. A brief that never
    # stated a mode for BMS therefore stayed silent however the planner answered, and BMS fell
    # back to its stage's discipline - fire - and was planned as somebody else's subcontract.
    # That breaks the rule the product exists for (CLAUDE.md rule 3).

    def _blank_disciplines(self) -> List[str]:
        """Disciplines the brief says nothing about, in a stable order."""
        stated = {
            str(k).lower() for k in (self.brief.get('delivery_mode_by_discipline') or {})
        }
        return [d for d in DELIVERY_DISCIPLINES if d not in stated]

    def _set_delivery_mode(self, discipline: str, mode: str) -> None:
        modes = self.brief.setdefault('delivery_mode_by_discipline', {})
        modes[discipline] = mode

    def _apply_delivery_mode(self, answer: DecisionAnswer) -> None:
        """Write a delivery-mode answer into the brief, without overwriting what it stated.

        A BARE ANSWER FILLS ONLY THE BLANKS. "Subcontract" answered to a question about every
        discipline is an inference over the gaps, not a correction of the brief, and the same
        principle that stops an unverified estimate speaking with the voice of a measurement
        applies here: a discipline the brief named keeps what the brief said, even when it sits
        squarely inside this fork's stage scope.
        """
        decision_id = answer.decision_point_id

        if decision_id.startswith('dyn.delivery_mode.'):
            # A follow-up about one discipline. It answers for that discipline and no other.
            discipline = decision_id.rsplit('.', 1)[-1]
            mode = read_delivery_mode(answer.answer)
            if mode and discipline in DELIVERY_DISCIPLINES:
                self._set_delivery_mode(discipline, mode)
            return

        if decision_id != 'dp.delivery_mode':
            return

        blanks = self._blank_disciplines()
        if MIXED_ANSWER in (answer.answer or '').lower():
            # "Mixed - specify per discipline" promises a breakdown, so collect one. Recording
            # the phrase and moving on is the same defect as dropping the answer entirely: the
            # option offers granularity and delivers none.
            #
            # One fork per blank discipline rather than free text: the modes are a closed set,
            # and a list of buttons cannot be mistyped or half-parsed. Nothing is asked about a
            # discipline the brief already stated - there is nothing to specify, and a question
            # the planner cannot usefully answer is noise.
            for discipline in blanks:
                self.state.pending_decisions[f'dyn.delivery_mode.{discipline}'] = {
                    'stage': self.state.pending_decisions.get(decision_id, {}).get('stage', ''),
                    'question': f'How is the {discipline} scope delivered on this project?',
                    'why_stuck': (
                        'The brief does not say, and "Mixed - specify per discipline" was chosen '
                        'rather than one mode for everything. A self-performed discipline '
                        'expands into a full fragnet of trade activities; a subcontracted one '
                        'collapses to an interface package.'
                    ),
                    'options': list(DELIVERY_MODE_OPTIONS),
                    'impact': f'Decides how every {discipline} activity is planned and resourced.',
                    'blocking': True,
                    'detection': 'dynamic',
                }
            return

        mode = read_delivery_mode(answer.answer)
        if not mode:
            return
        for discipline in blanks:
            self._set_delivery_mode(discipline, mode)


    # ------------------------------------------------------------------ site context
    #
    # The other half of the tier-1 safety fix. The engine raises `dp.greenfield_brownfield`
    # whenever the brief is silent; without this the answer would be recorded and discarded, the
    # safety register would still match nothing, and the fork would be theatre - the same defect
    # that made the delivery-mode fork worthless (CLAUDE.md rule 3).

    def _apply_site_context(self, answer: DecisionAnswer) -> None:
        """Write a site-context answer into the brief.

        No "fills only the blanks" rule needed here, unlike delivery modes: the fork is raised
        only when the brief is silent, so there is never stated content to overwrite. If a brief
        did state it, the question is not asked at all.
        """
        if answer.decision_point_id != SITE_CONTEXT_FORK:
            return
        if str(self.brief.get('site_context') or '').strip():
            return
        context = read_site_context(answer.answer)
        if context:
            self.brief['site_context'] = context

    def _resolved_decisions(self) -> List[Dict[str, Any]]:
        """Answers so far, in the shape the reasoning prompt injects as [DECISIONS_JSON]."""
        return [
            {'id': decision_id, **payload}
            for decision_id, payload in sorted(self.state.answers.items())
        ]

    # ------------------------------------------------------------------ assembly

    def _assemble_so_far(self) -> AssemblyResult:
        """Assemble every stage reasoned so far.

        Re-assembling rather than appending is deliberate: cross-stage gates (IFC gating
        procurement, delivery gating install) can only be wired once both ends exist, so an
        incremental append would leave earlier stages missing constraints a later stage
        introduces. Assembly is pure and cheap, so recomputing is the honest option.
        """
        ordered = [self.stage_reasonings[s] for s in self.stages if s in self.stage_reasonings]
        return assemble(ordered, self.brief, libraries=self.libraries)

    @property
    def result(self) -> AssemblyResult:
        return self._assemble_so_far()

    def output(self, questions: Optional[Sequence[str]] = None) -> SimulationOutput:
        """The one SimulationOutput the 2D view, the 3D/4D model and the P6 export project from.

        Callable on a halted run as well as a completed one: a partial simulation with open
        forks is a legitimate thing to render, and `quality.open_decision_count` plus
        `governance_complete` say plainly that it is not finished.
        """
        from backend.app.simulator.output import build_simulation_output

        return build_simulation_output(
            brief=self.brief,
            assembly=self._assemble_so_far(),
            stage_reasonings=[
                self.stage_reasonings[s] for s in self.stages if s in self.stage_reasonings
            ],
            resolved_decisions=self.state.answers,
            pending_decisions=self.state.pending_decisions,
            questions=questions,
            run_id=self.state.run_id,
            completed_stages=self.state.completed_stages,
        )

    @property
    def is_halted(self) -> bool:
        return self.state.is_halted

    @property
    def is_complete(self) -> bool:
        """Every stage of THIS walk is done.

        `RunState.is_complete` cannot answer this: the state knows which stages finished but not
        how many there were, so a run one stage into a thirteen-stage walk satisfies it. That is
        harmless where it is used - runs are only persisted at settle points, so a stored run is
        halted or finished - but it is the wrong question to ask when deciding what to tell a
        client reopening a run, because an in-memory run really can be mid-walk.
        """
        return (
            not self.state.is_halted
            and self.state.halted_at is None
            and set(self.stages).issubset(set(self.state.completed_stages))
        )


def run_to_completion(
    simulator: Simulator, answers: Dict[str, str], max_rounds: int = 20
) -> List[SimulationEvent]:
    """Drive a simulator to completion, answering forks from a prepared map.

    A test and CLI helper. It answers only decisions the simulator actually raises; a fork with
    no prepared answer stops the run, which is the correct behaviour rather than a failure.
    """
    events: List[SimulationEvent] = []
    for _ in range(max_rounds):
        events.extend(simulator.run())
        if not simulator.is_halted:
            return events
        answered = False
        for decision_id in sorted(simulator.state.pending_decisions):
            if decision_id in answers:
                simulator.answer(
                    DecisionAnswer(decision_point_id=decision_id, answer=answers[decision_id])
                )
                answered = True
        if not answered:
            return events
    return events
