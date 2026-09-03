/**
 * The 4D timeline: what exists on day N, and what state it is in.
 *
 * VISUALIZATION_SPEC.md section 2: "A time scrubber runs from start to RFS. As it advances, each
 * zone/element appears and changes state in step with its stage."
 *
 * Every number here comes from the engine (backend/app/engine/schedule.py). Nothing is derived
 * or estimated in the view: a schedule computed in the browser would be the view inventing dates,
 * and the 2D and 3D projections would then disagree about when the same activity happens.
 */

import type { SimulationOutput } from './types'

export type ZoneState = 'not_started' | 'in_progress' | 'complete'

export interface ZoneSpan {
  /** Day the zone comes into existence. */
  fromDay: number
  /** Day the last work in it finishes. */
  toDay: number
  /** Whether this came from zone-tagged activities or from the zone's stage. */
  source: 'activities' | 'stage' | 'unknown'
}

export interface Timeline {
  /** Last finish day across the plan — the far end of the scrubber. */
  rfsDay: number
  spans: Record<string, ZoneSpan>
  /** Stage spans, for the "what stage is the project in" readout. */
  stages: { stage: string; fromDay: number; toDay: number }[]
}

/**
 * Build the lookup the 3D model scrubs over.
 *
 * Two sources, in order of preference:
 *
 *  1. `zone_timeline` — derived from activities that name this zone. Precise, but only a few
 *     activities carry a `zone_id`, so most zones are not covered.
 *  2. `stage_timeline` — the span of the stage that brings the zone into existence
 *     (engine/zones.py ZONE_FIRST_STAGE). An approximation, and worth naming as one: it says
 *     "the data halls exist once superstructure runs", not "this hall was topped out on day 47".
 *
 * `source` records which was used, so the UI can be honest about precision rather than
 * presenting an approximation as a measurement.
 */
export function buildTimeline(output: SimulationOutput | null): Timeline {
  if (!output) return { rfsDay: 0, spans: {}, stages: [] }

  const zoneTimeline = (output.zone_timeline ?? {}) as Record<
    string,
    { first_day?: number; last_day?: number }
  >
  const stageTimeline = (output.stage_timeline ?? {}) as Record<
    string,
    { from_day?: number; to_day?: number }
  >

  const spans: Record<string, ZoneSpan> = {}
  for (const raw of output.zones ?? []) {
    const zone = raw as { id?: string; zone_id?: string; stage?: string }
    const id = String(zone.id ?? zone.zone_id ?? '')
    if (!id) continue

    const own = zoneTimeline[id]
    if (own && typeof own.first_day === 'number') {
      spans[id] = { fromDay: own.first_day, toDay: own.last_day ?? own.first_day, source: 'activities' }
      continue
    }

    const stage = stageTimeline[String(zone.stage ?? '')]
    if (stage && typeof stage.from_day === 'number') {
      spans[id] = { fromDay: stage.from_day, toDay: stage.to_day ?? stage.from_day, source: 'stage' }
      continue
    }

    // Neither: the zone's stage instanced nothing, so there is no evidence about when it is
    // built. Present from day 0 rather than hidden forever — hiding it would silently drop part
    // of the facility from the model.
    spans[id] = { fromDay: 0, toDay: output.rfs_day ?? 0, source: 'unknown' }
  }

  const stages = Object.entries(stageTimeline)
    .map(([stage, span]) => ({
      stage,
      fromDay: span.from_day ?? 0,
      toDay: span.to_day ?? 0,
    }))
    .sort((a, b) => a.fromDay - b.fromDay || a.stage.localeCompare(b.stage))

  return { rfsDay: output.rfs_day ?? 0, spans, stages }
}

/** What state a zone is in on a given day. */
export function zoneStateAt(span: ZoneSpan | undefined, day: number): ZoneState {
  if (!span) return 'not_started'
  if (day < span.fromDay) return 'not_started'
  // `>=` so a zone reads complete on the day its last activity finishes, not the day after.
  if (day >= span.toDay) return 'complete'
  return 'in_progress'
}

/** Stages running on a given day, for the scrubber's readout. */
export function stagesActiveAt(timeline: Timeline, day: number): string[] {
  return timeline.stages
    .filter((s) => day >= s.fromDay && day < s.toDay)
    .map((s) => s.stage)
}

/**
 * How far through the build a given day is, as a fraction.
 *
 * Guards a zero-length programme: a plan with no durations would otherwise divide by zero and
 * put the scrubber at NaN, which renders as a slider stuck at its left edge.
 */
export function progressAt(timeline: Timeline, day: number): number {
  if (timeline.rfsDay <= 0) return 0
  return Math.min(1, Math.max(0, day / timeline.rfsDay))
}
