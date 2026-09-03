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
  /** 4D timeline from the engine's forward pass (backend/app/engine/schedule.py). Day offsets
   *  from project start, not calendar dates — real dates arrive with the P6 export. */
  rfs_day?: number
  zone_timeline?: Record<string, unknown>
  stage_timeline?: Record<string, unknown>
  reasoning_trail: TrailEntry[]
  quality: Record<string, unknown>
  flags: Record<string, unknown>[]
}

// ---------------------------------------------------------------------------------------------
// Intake (backend/app/schemas.py). PRODUCT_SPEC.md section 3.1: the extracted brief carries a
// citation per field and lists what it still needs.
// ---------------------------------------------------------------------------------------------

export interface FieldProvenance {
  field: string
  quote: string
  confidence: number
  source_ref: string
  grounded: boolean
}

export interface IntakeQuestion {
  field: string
  question: string
  why_needed: string
  blocking: boolean
}

export interface ExtractedBrief {
  project_name: string | null
  client: string | null
  city: string | null
  site_context: string | null
  in_dc_park_or_sez: boolean | null
  tier: string | null
  redundancy_topology: string | null
  it_load_mw: number | null
  scope: string | null
  delivery_mode_by_discipline: Record<string, string>
  power_position: string | null
  target_rfs_date: string | null
  phasing: string | null
  special_conditions: string | null
}

export interface IntakeResult {
  brief: ExtractedBrief
  field_provenance: Record<string, FieldProvenance>
  questions: IntakeQuestion[]
  unresolved_fields: string[]
  flagged_conflicts: string[]
  extraction_confidence_overall: number
  warnings: string[]
  raw_brief_ref: string
  attachments: string[]
}

// ---------------------------------------------------------------------------------------------
// Simulation event stream (backend/app/simulator/events.py, SIMULATION_AND_REASONING.md §2).
// ---------------------------------------------------------------------------------------------

export type EventType =
  | 'simulation_started'
  | 'stage_started'
  | 'package_expanded'
  | 'activity_added'
  | 'gate_inserted'
  | 'decision_needed'
  | 'decision_resolved'
  | 'decision_recorded'
  | 'stage_completed'
  | 'simulation_halted'
  | 'simulation_completed'
  | 'simulation_error'

export interface SimulationEvent {
  seq: number
  type: EventType
  stage: string
  payload: Record<string, any>
}

/** A fork the simulator stopped at, as it arrives on the wire. */
export interface OpenDecision {
  id: string
  stage: string
  question: string
  why_stuck: string
  options: string[]
  impact: string
  blocking: boolean
  detection: string
}

export type RunStatus = 'idle' | 'running' | 'halted' | 'complete' | 'error'
