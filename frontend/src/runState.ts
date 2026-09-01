/**
 * Turns the simulation event stream into something renderable, as it arrives.
 *
 * VISUALIZATION_SPEC.md section 1: the flow "draws progressively... so a reviewer literally
 * watches the plan get built".
 *
 * Two sources of truth, deliberately:
 *
 *  - EVENTS build the graph incrementally, so nodes appear one by one while the run is mid-flight.
 *  - The AUTHORITATIVE SimulationOutput arrives with `simulation_halted` and
 *    `simulation_completed`, and replaces whatever the events built.
 *
 * The second matters. A client that only ever reconstructs from events is guessing at the object
 * the backend actually assembled — it would miss cross-stage edges wired during assembly, and any
 * drift would be silent. Events are for the animation; the output is for the truth.
 */

import type {
  FlowEdge,
  FlowNode,
  OpenDecision,
  RunStatus,
  SimulationEvent,
  SimulationOutput,
} from './types'

export interface RunState {
  status: RunStatus
  /** The server's id for this run. Needed to re-attach after a dropped socket or a restart. */
  runId: string
  /** Built from events while running; replaced by the authoritative output at each settle point. */
  nodes: FlowNode[]
  edges: FlowEdge[]
  /** Present once the backend has sent it. This is what the finished view renders. */
  output: SimulationOutput | null
  openDecisions: OpenDecision[]
  answered: { id: string; answer: string }[]
  stagesStarted: string[]
  stagesCompleted: string[]
  currentStage: string
  events: SimulationEvent[]
  error: string
}

export const INITIAL_RUN: RunState = {
  status: 'idle',
  runId: '',
  nodes: [],
  edges: [],
  output: null,
  openDecisions: [],
  answered: [],
  stagesStarted: [],
  stagesCompleted: [],
  currentStage: '',
  events: [],
  error: '',
}

function upsert(nodes: FlowNode[], node: FlowNode): FlowNode[] {
  return nodes.some((n) => n.id === node.id) ? nodes : [...nodes, node]
}

/** Fold one event into the run state. Pure, so the whole stream can be replayed. */
export function reduceEvent(state: RunState, event: SimulationEvent): RunState {
  const p = event.payload ?? {}
  const next: RunState = { ...state, events: [...state.events, event] }

  switch (event.type) {
    case 'simulation_started':
      // Keep the id: it is the only handle on a run that outlives this socket.
      return { ...next, status: 'running', error: '', runId: p.run_id ?? next.runId }

    case 'stage_started':
      return {
        ...next,
        status: 'running',
        currentStage: event.stage,
        stagesStarted: next.stagesStarted.includes(event.stage)
          ? next.stagesStarted
          : [...next.stagesStarted, event.stage],
        nodes: upsert(next.nodes, {
          id: `stage.${event.stage}`,
          kind: 'stage',
          stage: event.stage,
          label: event.stage.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()),
          dept: null,
          trail_ref: null,
          zone_id: null,
          parent: null,
        }),
      }

    case 'package_expanded':
      return {
        ...next,
        nodes: upsert(next.nodes, {
          id: `package.${event.stage}.${p.fragnet_id}`,
          kind: 'work_package',
          stage: event.stage,
          label: p.fragnet_id,
          dept: null,
          trail_ref: null,
          zone_id: null,
          parent: `stage.${event.stage}`,
          confidence: p.confidence,
          unverified_dependencies: p.unverified_dependencies ?? [],
        }),
      }

    case 'activity_added': {
      const node: FlowNode = {
        id: p.id,
        kind:
          p.type === 'milestone'
            ? 'milestone'
            : p.type === 'gate'
              ? 'compliance_gate'
              : p.type === 'hold_point'
                ? 'quality_hold'
                : 'activity',
        stage: event.stage,
        label: p.name,
        dept: p.dept_code ?? null,
        trail_ref: p.trail_ref ?? null,
        zone_id: p.zone_id ?? null,
        parent: `stage.${event.stage}`,
        wbs_id: p.wbs_id,
        duration_days: p.duration_days,
        safety_flag: p.safety_flag,
        hitl_tier: p.hitl_tier,
        blocks_export: p.blocks_export,
        confidence: p.confidence,
        unverified_dependencies: p.unverified_dependencies ?? [],
      }
      const newEdges: FlowEdge[] = (p.predecessors ?? []).map(
        (pred: { id: string; type: string; lag: number; kind: string }) => ({
          from: pred.id,
          to: p.id,
          type: pred.type,
          lag: pred.lag,
          kind: (pred.kind ?? 'fragnet') as FlowEdge['kind'],
          why: '',
        }),
      )
      return {
        ...next,
        nodes: upsert(next.nodes, node),
        edges: [...next.edges, ...newEdges.filter(
          (e) => !next.edges.some((x) => x.from === e.from && x.to === e.to && x.kind === e.kind),
        )],
      }
    }

    case 'gate_inserted':
      return {
        ...next,
        nodes: upsert(next.nodes, {
          id: `gate.${p.gate_id}`,
          kind: 'compliance_gate',
          stage: event.stage,
          label: p.gate_id,
          dept: null,
          trail_ref: null,
          zone_id: null,
          parent: `stage.${event.stage}`,
          confidence: p.confidence,
          unverified_dependencies: p.unverified_dependencies ?? [],
        }),
      }

    case 'decision_needed': {
      const decision: OpenDecision = {
        id: p.id,
        stage: event.stage,
        question: p.question,
        why_stuck: p.why_stuck,
        options: p.options ?? [],
        impact: p.impact ?? '',
        blocking: p.blocking ?? true,
        detection: p.detection ?? 'curated',
      }
      return {
        ...next,
        openDecisions: next.openDecisions.some((d) => d.id === decision.id)
          ? next.openDecisions
          : [...next.openDecisions, decision],
        nodes: upsert(next.nodes, {
          id: `decision.${p.id}`,
          kind: 'decision_point',
          stage: event.stage,
          label: p.question,
          dept: null,
          trail_ref: null,
          zone_id: null,
          parent: `stage.${event.stage}`,
          status: 'open',
          blocking: p.blocking,
          why_stuck: p.why_stuck,
          options: p.options ?? [],
          impact: p.impact ?? '',
          answer: null,
        }),
      }
    }

    case 'decision_resolved':
      return {
        ...next,
        openDecisions: next.openDecisions.filter((d) => d.id !== p.decision_point_id),
        answered: [...next.answered, { id: p.decision_point_id, answer: p.answer }],
        nodes: next.nodes.map((n) =>
          n.id === `decision.${p.decision_point_id}`
            ? { ...n, status: 'resolved' as const, answer: p.answer }
            : n,
        ),
      }

    // The backend recorded an answer but is still waiting on other forks at this stage.
    case 'decision_recorded':
      return { ...next, status: next.openDecisions.length ? 'halted' : 'running' }

    case 'stage_completed':
      return {
        ...next,
        stagesCompleted: next.stagesCompleted.includes(event.stage)
          ? next.stagesCompleted
          : [...next.stagesCompleted, event.stage],
      }

    // Both settle points carry the authoritative output. Adopt it wholesale rather than
    // trusting the graph reconstructed from events.
    case 'simulation_halted':
    case 'simulation_completed': {
      const output = (p.output ?? null) as SimulationOutput | null
      return {
        ...next,
        status: event.type === 'simulation_halted' ? 'halted' : 'complete',
        runId: p.run_id ?? output?.project_meta?.run_id ?? next.runId,
        output,
        nodes: output ? output.flow.nodes : next.nodes,
        edges: output ? output.flow.edges : next.edges,
      }
    }

    case 'simulation_error':
      return { ...next, status: 'error', error: p.error ?? 'The run failed.' }

    default:
      return next
  }
}
