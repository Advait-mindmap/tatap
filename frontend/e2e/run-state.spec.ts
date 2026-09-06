/**
 * The reducer, tested directly - no browser, no timing.
 *
 * Reported from automated browser testing: a run said "Stopped — needs your decision" and
 * "1 open decision point(s)" while no decision card existed anywhere in the DOM. It came with an
 * honest caveat - fast programmatic clicks, so possibly a speed artefact rather than a bug.
 *
 * It was a bug, and the caveat was the wrong thing to worry about. Open forks were tracked in
 * TWO places: `openDecisions`, built from decision_needed events and pruned optimistically the
 * moment a user clicks, and the decision_point nodes inside the authoritative output, which is
 * what the badge counts. Two sources for one fact disagree eventually; click speed only decides
 * how often.
 *
 * A browser test cannot pin this reliably, because reproducing it depends on winning a race.
 * The reducer is a pure function, so the disagreement can be constructed exactly.
 */

import { expect, test } from '@playwright/test'

import { INITIAL_RUN, reduceEvent, type RunState } from '../src/runState'
import type { SimulationEvent } from '../src/types'

let seq = 0
const event = (type: string, stage: string, payload: Record<string, unknown>): SimulationEvent =>
  ({ seq: (seq += 1), type, stage, payload }) as unknown as SimulationEvent

/** A decision_point node as the engine puts it in the output. */
const forkNode = (id: string, status: 'open' | 'resolved') => ({
  id: `decision.${id}`,
  kind: 'decision_point' as const,
  stage: 'procurement',
  label: `Question for ${id}?`,
  dept: null,
  trail_ref: null,
  zone_id: null,
  status,
  blocking: true,
  why_stuck: `Stuck on ${id}`,
  options: ['Option A', 'Option B'],
  impact: 'Changes the plan',
  answer: null,
})

const outputWith = (nodes: ReturnType<typeof forkNode>[]) => ({
  project_meta: { run_id: 'run-test' },
  flow: { nodes, edges: [] },
  quality: {},
  activities: [],
  zones: [],
  decisions: [],
  questions: [],
  statutory_pathway: [],
  equipment_counts: [],
  long_lead_register: [],
  commissioning: [],
  reasoning_trail: [],
  flags: [],
  rfs_day: 0,
  zone_timeline: [],
  stage_timeline: [],
})

const drive = (events: SimulationEvent[], from: RunState = INITIAL_RUN) =>
  events.reduce((state, e) => reduceEvent(state, e), from)

/** The invariant, stated once. */
function assertCoherent(state: RunState, when: string) {
  const badge = (state.output?.flow.nodes ?? []).filter(
    (n) => n.kind === 'decision_point' && n.status === 'open',
  ).length
  if (state.status !== 'halted' && badge === 0) return
  expect(
    state.openDecisions.length,
    `${when}: status="${state.status}", the badge would show ${badge} open fork(s), ` +
      `but openDecisions is empty — the reader is told a decision is needed and shown none`,
  ).toBeGreaterThan(0)
}

test('a halted run always carries the fork it is halted on', () => {
  const state = drive([
    event('simulation_started', '', { run_id: 'run-test', zones: [] }),
    event('decision_needed', 'procurement', {
      id: 'dp.ofe',
      question: 'Owner-furnished equipment?',
      why_stuck: 'The brief does not say',
      options: ['Owner-furnished', 'Contractor-furnished'],
      impact: 'Changes procurement',
      blocking: true,
      detection: 'curated',
    }),
    event('simulation_halted', 'procurement', {
      run_id: 'run-test',
      pending: ['dp.ofe'],
      output: outputWith([forkNode('dp.ofe', 'open')]),
    }),
  ])

  expect(state.status).toBe('halted')
  expect(state.openDecisions.map((d) => d.id)).toEqual(['dp.ofe'])
  assertCoherent(state, 'at the halt')
})

test('the output decides which forks are open, not the events alone', () => {
  // The reported state, constructed exactly: the client has optimistically cleared its list -
  // as answer() does the instant a user clicks - and THEN an authoritative halt arrives saying
  // a fork is still open. Before the fix the card list stayed empty while the badge said one,
  // and the run was unanswerable.
  const cleared: RunState = {
    ...INITIAL_RUN,
    status: 'running',
    runId: 'run-test',
    openDecisions: [],
  }

  const state = reduceEvent(
    cleared,
    event('simulation_halted', 'procurement', {
      run_id: 'run-test',
      pending: ['dp.grid_position'],
      output: outputWith([forkNode('dp.grid_position', 'open')]),
    }),
  )

  expect(state.status).toBe('halted')
  expect(
    state.openDecisions.map((d) => d.id),
    'the halt carried an open fork and the client did not adopt it',
  ).toEqual(['dp.grid_position'])
  // Reconstructed from the node, so the card is answerable rather than a bare title.
  expect(state.openDecisions[0].options).toEqual(['Option A', 'Option B'])
  expect(state.openDecisions[0].why_stuck).toBe('Stuck on dp.grid_position')
  assertCoherent(state, 'after adopting an authoritative halt')
})

test('a resolved fork is dropped even if the client still lists it', () => {
  const stale: RunState = {
    ...INITIAL_RUN,
    status: 'halted',
    runId: 'run-test',
    openDecisions: [
      {
        id: 'dp.ofe',
        stage: 'procurement',
        question: 'q',
        why_stuck: 'w',
        options: [],
        impact: '',
        blocking: true,
        detection: 'curated',
      },
    ],
  }

  const state = reduceEvent(
    stale,
    event('simulation_halted', 'procurement', {
      run_id: 'run-test',
      pending: [],
      output: outputWith([forkNode('dp.ofe', 'resolved')]),
    }),
  )

  expect(state.openDecisions, 'a resolved fork is still being offered').toEqual([])
})

test('decision_recorded follows the server on what is still open', () => {
  // Two forks raised, one answered. The server replies with what remains; the client must
  // narrow to that rather than trusting its own optimistically-edited list.
  const both = drive([
    event('decision_needed', 'procurement', {
      id: 'dp.ofe', question: 'a', why_stuck: 'a', options: ['x'], impact: '', blocking: true,
    }),
    event('decision_needed', 'procurement', {
      id: 'dp.delivery_mode', question: 'b', why_stuck: 'b', options: ['y'], impact: '',
      blocking: true,
    }),
  ])
  expect(both.openDecisions).toHaveLength(2)

  const after = reduceEvent(
    both,
    event('decision_recorded', '', {
      decision_point_id: 'dp.ofe',
      pending: ['dp.delivery_mode'],
    }),
  )
  expect(after.status).toBe('halted')
  expect(after.openDecisions.map((d) => d.id)).toEqual(['dp.delivery_mode'])
})

test('answering the last fork lets the run continue', () => {
  const one = drive([
    event('decision_needed', 'procurement', {
      id: 'dp.ofe', question: 'a', why_stuck: 'a', options: ['x'], impact: '', blocking: true,
    }),
  ])
  const after = reduceEvent(
    one,
    event('decision_recorded', '', { decision_point_id: 'dp.ofe', pending: [] }),
  )
  expect(after.status).toBe('running')
  expect(after.openDecisions).toEqual([])
})

test('a fork the user already answered is never re-offered by a stale halt', () => {
  // The regression the reconciliation introduced, and the reason live-3d died with
  // "dp.x is not an open decision on this run. Open: []".
  //
  // A halted event is HISTORY - it says what was open when the server sent it. A client that is
  // paused or stepping is deliberately behind, so adopting an old halt can resurrect a fork the
  // user has since answered. Clicking it again ends the run.
  const halted = drive([
    event('decision_needed', 'enabling', {
      id: 'dp.greenfield_brownfield',
      question: 'Greenfield or brownfield?',
      why_stuck: 'The brief is ambiguous',
      options: ['Greenfield', 'Brownfield'],
      impact: 'Changes enabling works',
      blocking: true,
    }),
    event('simulation_halted', 'enabling', {
      run_id: 'run-test',
      pending: ['dp.greenfield_brownfield'],
      output: outputWith([forkNode('dp.greenfield_brownfield', 'open')]),
    }),
  ])
  expect(halted.openDecisions).toHaveLength(1)

  // The user answers. App.answer() records it optimistically, which is what this simulates.
  const answeredLocally: RunState = {
    ...halted,
    status: 'running',
    openDecisions: [],
    answered: [{ id: 'dp.greenfield_brownfield', answer: 'Greenfield' }],
  }

  // A halt from BEFORE that answer now drains - the client was paused, so it is behind.
  const afterStaleHalt = reduceEvent(
    answeredLocally,
    event('simulation_halted', 'enabling', {
      run_id: 'run-test',
      pending: ['dp.greenfield_brownfield'],
      output: outputWith([forkNode('dp.greenfield_brownfield', 'open')]),
    }),
  )

  expect(
    afterStaleHalt.openDecisions.map((d) => d.id),
    'a stale halt re-offered a fork the user had already answered — answering it again kills ' +
      'the run',
  ).toEqual([])
})

test('the server confirming an answer does not duplicate it', () => {
  const state: RunState = {
    ...INITIAL_RUN,
    answered: [{ id: 'dp.ofe', answer: 'Owner-furnished' }],
  }
  const after = reduceEvent(
    state,
    event('decision_resolved', 'procurement', {
      decision_point_id: 'dp.ofe',
      answer: 'Owner-furnished',
    }),
  )
  expect(after.answered).toHaveLength(1)
  expect(after.answered[0]).toEqual({ id: 'dp.ofe', answer: 'Owner-furnished' })
})
