/**
 * The SimulationOutput contract, mirroring SIMULATION_AND_REASONING.md section 7 and the
 * pydantic models in backend/app/schemas.py.
 *
 * One simulation, many projections: `flow` is what this 2D view reads. `zones` drive the 3D/4D
 * model and `activities` export to P6 — same object, different projections.
 */

/** Node kinds the 2D view must be able to tell apart (VISUALIZATION_SPEC.md section 1). */
export type NodeKind =
  | 'stage'
  | 'work_package'
  | 'activity'
  | 'milestone'
  | 'compliance_gate'
  | 'quality_hold'
  | 'decision_point'

export interface FlowNode {
  id: string
  kind: NodeKind
  stage: string
  label: string
  dept: string | null
  trail_ref: string | null
  zone_id: string | null
  parent?: string | null
  wbs_id?: string
  duration_days?: number
  safety_flag?: boolean
  hitl_tier?: string
  blocks_export?: boolean
  confidence?: number
  unverified_dependencies?: string[]
  start?: string | null
  finish?: string | null
  /** decision_point only */
  status?: 'open' | 'resolved'
  blocking?: boolean
  why_stuck?: string
  options?: string[]
  impact?: string
  answer?: string | null
}

/** Edge kinds carry meaning: a delivery constraint is not ordinary fragnet logic. */
export type EdgeKind = 'fragnet' | 'cross_stage_gate' | 'delivery' | 'compliance' | 'hold_point'

export interface FlowEdge {
  from: string
  to: string
  type: string
  lag: number
  kind: EdgeKind
  why: string
}

export interface TrailEntry {
  ref_id: string
  stage: string
  decision: string | null
  why: string
  sources: string[]
  confidence: number
  decided_by: string
  hitl_tier: string
  unverified_dependencies: string[]
  stated_confidence: number | null
}

export interface DecisionRecord {
  id: string
  question: string
  answer: string
  impact: string
}

export interface SimulationOutput {
  project_meta: Record<string, unknown>
  questions: string[]
  decisions: DecisionRecord[]
  flow: { nodes: FlowNode[]; edges: FlowEdge[] }
  statutory_pathway: Record<string, unknown>[]
  equipment_counts: Record<string, unknown>[]
  long_lead_register: Record<string, unknown>[]
  activities: Record<string, unknown>[]
  commissioning: Record<string, unknown>[]
  zones: Record<string, unknown>[]
  reasoning_trail: TrailEntry[]
  quality: Record<string, unknown>
  flags: Record<string, unknown>[]
}
