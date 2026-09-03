import { stagesActiveAt, type Timeline } from '../timeline'

interface Props {
  timeline: Timeline
  day: number
  onChange: (day: number) => void
  /**
   * True while the run is still assembling the plan.
   *
   * The timeline is computed from what has been assembled SO FAR, so RFS grows as the walk
   * proceeds - it read "Day 170 of 170" partway through a run that finished at 629. A reader
   * glancing at that would take 170 for the completion date. Marked as provisional, the same
   * number is informative rather than misleading.
   */
  provisional?: boolean
}

/**
 * The 4D time scrubber: project start on the left, RFS on the right.
 *
 * Days, not dates. The engine computes earliest start and finish in whole days from day 0
 * (engine/schedule.py); it does not yet map those onto a calendar, and putting invented dates on
 * the axis would present a precision the plan does not have. When the P6 export supplies real
 * dates, the labels change and nothing else does.
 */
export function TimeScrubber({ timeline, day, onChange, provisional = false }: Props) {
  const active = stagesActiveAt(timeline, day)
  const pct = timeline.rfsDay > 0 ? Math.round((day / timeline.rfsDay) * 100) : 0

  return (
    <div className="scrubber" data-testid="time-scrubber" data-provisional={String(provisional)}>
      <div className="scrubber-head">
        <span className="scrubber-day" data-testid="scrubber-day">
          Day {day} of {timeline.rfsDay}
          {provisional && ' so far'}
        </span>
        {provisional ? (
          // No "% to RFS" while RFS is still moving: a percentage of an unknown total is a
          // number with no meaning, and it is the reassuring-looking kind.
          <span
            className="scrubber-pct scrubber-provisional"
            data-testid="scrubber-provisional"
            title="The plan is still being assembled; the end date will move as later stages are reasoned."
          >
            provisional — still assembling
          </span>
        ) : (
          <span className="scrubber-pct mono">{pct}% to RFS</span>
        )}
        <span className="scrubber-stages" data-testid="scrubber-stages">
          {active.length ? active.join(' · ').replace(/_/g, ' ') : 'no stage in progress'}
        </span>
      </div>

      <input
        className="scrubber-range"
        data-testid="scrubber-range"
        type="range"
        min={0}
        max={Math.max(timeline.rfsDay, 1)}
        value={day}
        aria-label="Build timeline"
        onChange={(e) => onChange(Number(e.target.value))}
      />

      <div className="scrubber-ends">
        <button
          className="scrubber-jump"
          data-testid="scrubber-start"
          onClick={() => onChange(0)}
        >
          Start
        </button>
        <button
          className="scrubber-jump"
          data-testid="scrubber-rfs"
          onClick={() => onChange(timeline.rfsDay)}
        >
          {provisional ? 'Latest' : 'RFS'}
        </button>
      </div>
    </div>
  )
}
