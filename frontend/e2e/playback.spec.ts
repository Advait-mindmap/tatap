/**
 * Task 12: progressive draw controls — play, pause, step, replay.
 *
 * The point of these controls is that a reviewer can WATCH the plan get built
 * (VISUALIZATION_SPEC.md section 1), so every assertion here is about the graph changing or
 * failing to change on command. Counting buttons would prove nothing: the controls can all be
 * present, wired to handlers, and still move no nodes.
 *
 * Replay is the deterministic vehicle. A live stream arrives when it arrives, so pausing "mid
 * draw" is a race; replaying a finished run is driven entirely by the client, which makes the
 * progressive draw observable and repeatable.
 *
 * Run against the stub API so the run is fast, free and deterministic:
 *     LLM_PROVIDER=stub python -m uvicorn backend.app.main:app --port 8000
 */

import { expect, test, type Page } from '@playwright/test'

import { apiReachable, completedRun } from './support'

const SHOTS = 'e2e/screenshots'

test.beforeAll(async () => {
  test.skip(!(await apiReachable()), 'no API on :8000 — start it with LLM_PROVIDER=stub')
})

const nodeCount = (page: Page) => page.locator('[data-testid="node-card"]').count()

/**
 * Wait for the drain to finish, then read the count.
 *
 * Waiting for the node count to stop changing is not the same thing and was flaky: only some
 * events add a node, so two consecutive equal readings happen readily in the middle of a
 * replay, and under the load of a full suite run that window widens. The panel shows a
 * "N queued" chip exactly while events are waiting, so its absence is the real signal.
 */
async function settled(page: Page, timeout = 30_000): Promise<number> {
  await expect(page.getByTestId('queued-count')).toBeHidden({ timeout })
  await page.waitForTimeout(200)
  return nodeCount(page)
}

test('replay redraws the plan progressively rather than snapping to the end', async ({ page }) => {
  await completedRun(page)
  const finished = await nodeCount(page)
  expect(finished, 'the run drew nothing to replay').toBeGreaterThan(5)

  await expect(page.getByTestId('playback-controls')).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/12-01-complete-before-replay.png` })

  await page.getByTestId('replay-btn').click()

  // The graph must go back to (nearly) empty and grow again. A replay that recomputes the same
  // final state in one step is indistinguishable from doing nothing.
  const samples: number[] = []
  for (let i = 0; i < 14; i += 1) {
    samples.push(await nodeCount(page))
    await page.waitForTimeout(220)
  }

  const low = Math.min(...samples)
  const high = Math.max(...samples)
  expect(low, `replay never cleared the graph (samples: ${samples.join(',')})`).toBeLessThan(
    finished,
  )
  expect(high, `replay never rebuilt the graph (samples: ${samples.join(',')})`).toBeGreaterThan(
    low,
  )
  // Progressive means several distinct intermediate sizes, not empty-then-full in one jump.
  const distinct = new Set(samples).size
  expect(distinct, `the graph jumped rather than drew (samples: ${samples.join(',')})`)
    .toBeGreaterThanOrEqual(3)

  await page.screenshot({ path: `${SHOTS}/12-02-replay-midway.png` })
})

test('pause freezes the draw and play resumes it', async ({ page }) => {
  await completedRun(page)
  const finished = await nodeCount(page)

  await page.getByTestId('replay-btn').click()
  await page.waitForTimeout(500) // let a few events draw

  await page.getByTestId('pause-btn').click()
  await expect(page.getByTestId('play-btn')).toBeVisible() // the control flipped
  const atPause = await nodeCount(page)
  await page.screenshot({ path: `${SHOTS}/12-03-paused.png` })

  // Frozen: the queue keeps filling but nothing is applied to the graph.
  await page.waitForTimeout(1500)
  const stillPaused = await nodeCount(page)
  expect(stillPaused, 'the graph kept drawing while paused').toBe(atPause)

  await page.getByTestId('play-btn').click()
  const resumed = await settled(page)
  expect(resumed, 'play did not resume the draw').toBeGreaterThan(atPause)
  expect(resumed).toBe(finished) // and it lands on the same plan it started from
  await page.screenshot({ path: `${SHOTS}/12-04-resumed.png` })
})

test('step advances the draw one event at a time', async ({ page }) => {
  await completedRun(page)

  await page.getByTestId('replay-btn').click()
  await page.waitForTimeout(400)
  await page.getByTestId('pause-btn').click()
  await page.waitForTimeout(400)

  const before = await nodeCount(page)
  const eventsBefore = Number(
    (await page.getByTestId('event-counter').textContent())?.match(/\d+/)?.[0] ?? '0',
  )

  // One click must consume exactly one event. Node count may not move (not every event adds a
  // node — stage_started, package_expanded and the rest), so the event counter is what proves
  // a single step happened.
  await page.getByTestId('step-btn').click()
  await page.waitForTimeout(300)
  const eventsAfter = Number(
    (await page.getByTestId('event-counter').textContent())?.match(/\d+/)?.[0] ?? '0',
  )
  expect(eventsAfter, 'step did not apply exactly one event').toBe(eventsBefore + 1)

  // Stepping enough times must eventually add nodes.
  for (let i = 0; i < 25; i += 1) {
    await page.getByTestId('step-btn').click()
  }
  await page.waitForTimeout(400)
  const after = await nodeCount(page)
  expect(after, 'stepping never drew anything').toBeGreaterThan(before)
  await page.screenshot({ path: `${SHOTS}/12-05-stepped.png` })
})

test('the controls stay reachable once the run has finished', async ({ page }) => {
  // Replay is most useful precisely when the run is over. Hiding the controls on completion
  // makes the feature unreachable exactly when it is wanted.
  await completedRun(page)
  await expect(page.getByTestId('run-status')).toHaveText(/Simulation complete/)
  await expect(page.getByTestId('playback-controls')).toBeVisible()
  await expect(page.getByTestId('replay-btn')).toBeEnabled()
})
