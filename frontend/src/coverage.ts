/**
 * Which stages the plan does not actually cover.
 *
 * A run can finish with "Simulation complete" and 371 activities while three whole stages were
 * never planned. That happens legitimately: the reasoner stops at a low-confidence fork, the
 * planner answers "Stop and obtain real data", and the follow-up coverage fork is answered "In
 * scope - record the plan as incomplete". Both answers are honoured exactly, and the result is a
 * knowingly incomplete plan.
 *
 * The problem was that it did not LOOK incomplete. The only traces were `blocks_export` flags and
 * an `[UNANCHORED]` gate name a reader had to go and find. A real run reached this state with
 * mep_power, commissioning and procurement empty, and the reported symptom was "the commissioning
 * gate has no predecessors" - a reader diagnosing gate wiring when three stages had simply not
 * been planned.
 *
 * COUNTED FROM THE PLAN, not from a warning string. A stage is unplanned when it produced no
 * TASKS. Gates and delivery milestones still carry a stage, so counting activities would call
 * procurement covered on the strength of nine delivery milestones with no procurement work behind
 * them - which is exactly the case that misleads. Statutory approvals are real tasks with real
 * durations, so a stage carrying only those is genuinely covered and is not listed.
 */

import type { SimulationOutput } from './types'

/** Stage keys, in the order a reader expects to see them. */
const STAGE_ORDER = [
  'design',
  'approvals',
  'procurement',
  'enabling',
  'substructure',
  'superstructure',
  'envelope',
  'mep_power',
  'mep_cooling',
  'fire_bms',
  'fit_out',
  'commissioning',
  'handover',
]

export interface Coverage {
  /** Stages the run walked but which produced no work. */
  unplanned: string[]
  /** Stages that did produce work. */
  planned: string[]
  /** True when anything in the plan refuses to export until a human signs it off. */
  exportBlocked: boolean
}

function stageRank(stage: string): number {
  const index = STAGE_ORDER.indexOf(stage)
  return index === -1 ? STAGE_ORDER.length : index
}

export function readCoverage(output: SimulationOutput | null): Coverage {
  const empty: Coverage = { unplanned: [], planned: [], exportBlocked: false }
  if (!output) return empty

  const activities = (output.activities ?? []) as Record<string, unknown>[]
  if (!activities.length) return empty

  const walked = new Set<string>()
  const withWork = new Set<string>()
  let exportBlocked = false

  for (const activity of activities) {
    const stage = String(activity.stage ?? '')
    if (!stage) continue
    walked.add(stage)
    if (activity.type === 'task') withWork.add(stage)
    if (activity.blocks_export === true) exportBlocked = true
  }

  // A stage the run never reached is not "unplanned", it is unstarted - and a halted run would
  // otherwise report every stage ahead of it as missing, which is noise rather than a warning.
  const completed = new Set(
    ((output.project_meta?.stages_completed as string[] | undefined) ?? []).map(String),
  )
  const considered = completed.size ? completed : walked

  // No tasks is no tasks, whether the stage produced nothing at all or only gates and delivery
  // milestones. Requiring it to have SOME activity was the first version, and it excluded the
  // worst case: a stage that instanced literally nothing has no activities to be found by, and
  // was therefore reported as covered.
  const unplanned = [...considered].filter((stage) => !withWork.has(stage))
  const planned = [...considered].filter((stage) => withWork.has(stage))

  return {
    unplanned: unplanned.sort((a, b) => stageRank(a) - stageRank(b)),
    planned: planned.sort((a, b) => stageRank(a) - stageRank(b)),
    exportBlocked,
  }
}

/** `mep_power` -> `MEP power`, for a banner a person reads rather than greps. */
export function stageLabel(stage: string): string {
  const words = stage.replace(/_/g, ' ')
  return words.replace(/\bmep\b/i, 'MEP').replace(/^./, (c) => c.toUpperCase())
}
