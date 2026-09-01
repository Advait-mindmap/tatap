/**
 * Deterministic layered layout: one column per stage, in execution order.
 *
 * No layout library. The graph is already layered by construction — the simulator walks stages
 * in order and dependencies run left to right — so a column per stage reads as the programme
 * rather than as a force-directed cloud, and it stays stable between renders. A stable layout
 * matters more than a pretty one here: a planner returning to a node needs it where they left it.
 */

import type { FlowNode, NodeKind } from './types'

export const COLUMN_WIDTH = 292
/** Must exceed the bounded card height (92px in styles.css) or cards overlap and steal each
 *  other's pointer events. */
export const ROW_HEIGHT = 104
export const HEADER_Y = 0

/** Vertical grouping within a stage column, top to bottom. */
const KIND_BAND: Record<NodeKind, number> = {
  stage: 0,
  decision_point: 1, // directly under the stage header: the fork must be impossible to miss
  work_package: 2,
  activity: 3,
  quality_hold: 4,
  milestone: 5,
  compliance_gate: 6,
}

export interface Positioned extends FlowNode {
  position: { x: number; y: number }
}

/** Stage order taken from the data itself, so the view never disagrees with the walk. */
export function stageOrder(nodes: FlowNode[]): string[] {
  const seen: string[] = []
  for (const node of nodes) {
    if (node.kind === 'stage' && !seen.includes(node.stage)) seen.push(node.stage)
  }
  // Any stage that produced nodes but no stage header (defensive) still gets a column.
  for (const node of nodes) {
    if (node.stage && !seen.includes(node.stage)) seen.push(node.stage)
  }
  return seen
}

export function layout(nodes: FlowNode[]): Positioned[] {
  const stages = stageOrder(nodes)
  const nextRow = new Map<string, number>()

  const sorted = [...nodes].sort((a, b) => {
    const band = KIND_BAND[a.kind] - KIND_BAND[b.kind]
    if (band !== 0) return band
    // Within a band, WBS order where present, then id — deterministic either way.
    const wbs = (a.wbs_id ?? '').localeCompare(b.wbs_id ?? '')
    return wbs !== 0 ? wbs : a.id.localeCompare(b.id)
  })

  return sorted.map((node) => {
    const column = Math.max(0, stages.indexOf(node.stage))
    const row = nextRow.get(node.stage) ?? 0
    nextRow.set(node.stage, row + 1)
    return {
      ...node,
      position: { x: column * COLUMN_WIDTH, y: HEADER_Y + row * ROW_HEIGHT },
    }
  })
}
