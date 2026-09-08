/**
 * Reattaching must land on the server's state, and must survive doing it twice in a row.
 *
 * Reported from a live run: after a reload the panel read "4 events" and showed the first
 * decision point, which looked like a run that had reverted. The server was checked directly and
 * had not - it held seq 30, halted in approvals, with THREE open forks and 21 activities.
 *
 * "4 events" is very likely correct arithmetic rather than a regression: attach replays one
 * `decision_needed` per open fork plus one `simulation_halted`, which for three forks is exactly
 * four. Showing the first of three is right too. But "very likely" is not a thing to leave in a
 * bug report, so the first test here pins what a reattached client must actually show - the
 * server's plan, not a number that happens to look small.
 *
 * The second is the real risk, and it comes from the reconnect path added for the dropped-stream
 * bug: that path retries up to three times, and `attachTo` clears `allEventsRef` and `queueRef`
 * on EVERY attempt. Two attaches landing close together can interleave - one clearing the
 * buffers while the other's events are mid-drain. `onClose` is guarded by a connection token;
 * `onEvent` is not.
 */

import { expect, test } from '@playwright/test'
import { apiReachable, extractBrief } from './support'

test.beforeAll(async () => {
  test.skip(!(await apiReachable()), 'planner API not reachable - start uvicorn to run these')
})

/** Watch what the SERVER sends, so assertions compare against it rather than against a guess. */
type Seen = { activities: number; forks: Set<string>; halts: number; attaches: number }

async function watchServer(page: import('@playwright/test').Page, dropFirst: number) {
  const seen: Seen = { activities: 0, forks: new Set(), halts: 0, attaches: 0 }
  await page.routeWebSocket(/\/ws\/simulate/, (ws) => {
    const server = ws.connectToServer()
    let dropped = false
    ws.onMessage((message) => {
      if (String(message).includes('"attach"')) seen.attaches += 1
      server.send(message)
    })
    server.onMessage((message) => {
      const text = String(message)
      if (text.includes('"decision_needed"')) {
        try {
          seen.forks.add(JSON.parse(text).payload.id)
        } catch {
          /* not fatal for the count */
        }
      }
      if (text.includes('"simulation_halted"')) {
        seen.halts += 1
        try {
          const payload = JSON.parse(text).payload
          seen.activities = (payload.output?.activities ?? []).length
        } catch {
          /* leave the previous count */
        }
        // Cut the first `dropFirst` halts so the client's reconnect path runs. Each cut is a
        // separate drop, so two of them exercise reattaches landing back to back.
        if (!dropped && seen.halts <= dropFirst) {
          dropped = true
          ws.close({ code: 1006, reason: 'drop' })
          return
        }
      }
      ws.send(message)
    })
  })
  return seen
}

/**
 * Wait until the plan stops changing.
 *
 * A halted run is not necessarily a still one: answering resumes the walk, and the queue drains
 * for a while after the status says "needs your decision". Comparing a moving run against itself
 * across a reload reports differences that are just progress.
 */
async function settle(page: import('@playwright/test').Page, quietMs = 2_000) {
  let previous = -1
  for (let i = 0; i < 40; i += 1) {
    const nodes = await page.getByTestId('node-card').count()
    if (nodes === previous) return nodes
    previous = nodes
    await page.waitForTimeout(quietMs)
  }
  return previous
}

test('a reattached client shows the plan the server actually holds', async ({ page }) => {
  test.setTimeout(600_000)

  const seen = await watchServer(page, 0)
  await extractBrief(page)
  await page.getByTestId('run-button').click()
  await expect(page.getByTestId('decision-prompt')).toBeVisible({ timeout: 300_000 })

  const runId = new URL(page.url()).searchParams.get('run')
  expect(runId).toMatch(/^run-/)

  // SETTLE BEFORE MEASURING. A halted run is not a still one - the event queue drains after the
  // prompt appears, so counting immediately catches the client mid-draw. This test captured
  // `before` the instant the prompt showed and passed on a fast machine and failed on CI with
  // before=3, after=26: not a lost plan, a plan that had not finished arriving. The sibling test
  // below already learned this; fixing it there and not here left the same flaw one test over.
  const before = await settle(page)

  // Reload: the reported action, and the one the reconnect path imitates.
  await page.reload()
  await expect(page.getByTestId('run-status')).toHaveText(/needs your decision/i, {
    timeout: 300_000,
  })
  const after = await settle(page)
  console.log(
    `  nodes before reload ${before}, after ${after}; server sent ${seen.activities} activities, ` +
      `${seen.forks.size} fork(s)`,
  )

  // THE POINT: the recovered view is the server's plan, not a fraction of it. Counting node
  // cards rather than the event badge, because the badge legitimately counts the attach replay -
  // which is what made this look like a reverted run.
  // EQUAL, not "at least something". The reported symptom was a view that had gone BACKWARDS,
  // and an inequality against the smaller of two numbers would pass on exactly that. The run did
  // not advance across a reload, so the recovered plan is the same plan.
  expect(after, 'the reattached client drew a different plan than it had before the reload')
    .toBe(before)

  // Every open fork the server re-raised is answerable, not just the first one.
  const options = await page.getByTestId('decision-option').count()
  expect(options, 'the recovered fork offers nothing to choose').toBeGreaterThan(0)

  // AND the client holds all of them. The reported run had THREE open forks and showed the
  // first, which is correct behaviour - but only if the other two are still there behind it.
  // A client that kept one of three would look identical on screen and lose two answers.
  if (seen.forks.size > 1) {
    const held = await page
      .getByTestId('more-decisions')
      .getAttribute('data-open-count')
    expect(
      Number(held),
      `the server re-raised ${seen.forks.size} forks; the client holds ${held}`,
    ).toBe(seen.forks.size)
  } else {
    console.log('  only one fork in this run - the multi-fork path is not exercised here')
  }
})

test('two reattaches landing back to back still leave the run in the server state', async ({
  page,
}) => {
  test.setTimeout(900_000)

  // Two drops: the client reconnects, and that reconnection is dropped once more, so the second
  // attach begins while the first is still settling.
  // Baseline: the same brief, reattached once with no drops. Whatever that yields is what the
  // dropped-and-retried version has to match.
  const cleanSeen = await watchServer(page, 0)
  await extractBrief(page)
  await page.getByTestId('run-button').click()
  await expect(page.getByTestId('decision-prompt')).toBeVisible({ timeout: 300_000 })
  await page.reload()
  await expect(page.getByTestId('run-status')).toHaveText(/needs your decision/i, {
    timeout: 300_000,
  })
  const clean = await settle(page)
  console.log(`  clean reattach baseline: ${clean} nodes (${cleanSeen.attaches} attach)`)

  // Now the same thing again, with the stream dropped twice so reattaches overlap.
  const seen = await watchServer(page, 2)
  await extractBrief(page)
  await page.getByTestId('run-button').click()

  await expect
    .poll(() => seen.attaches, { timeout: 600_000, message: 'the client never reattached' })
    .toBeGreaterThanOrEqual(2)

  const status = page.getByTestId('run-status')
  await expect(status, 'overlapping reattaches left the run stuck').not.toHaveText(/Simulating/, {
    timeout: 300_000,
  })
  const nodes = await settle(page)
  const text = (await status.textContent()) ?? ''
  console.log(
    `  after ${seen.attaches} attaches: status "${text.trim()}", ${nodes} nodes, ` +
      `server sent ${seen.activities} activities`,
  )

  // The interleave to catch: buffers cleared by one attach while another's events drain, which
  // shows as a plan smaller than the one the server sent.
  // Compared against a CLEAN attach of the same run, not against 1. `Math.min(1, ...)` was the
  // first version of this line and it would have passed on a single node - the very outcome it
  // was meant to catch.
  expect(nodes, 'overlapping reattaches left the client with a smaller plan than a clean one')
    .toBe(clean)
  expect(text).toMatch(/decision|complete|stopped|connection|error/i)
})

test('a reload with several forks open keeps every one of them', async ({ page }) => {
  test.setTimeout(900_000)

  // The reported condition exactly: a run halted in approvals with THREE open forks, reloaded.
  // The panel shows the first, which is right - but only if the other two survive behind it. A
  // client that kept one of three looks identical on screen and silently loses two answers.
  //
  // The default brief halts first on a single fork, so this answers forward until the run stops
  // somewhere that raises more than one at a time. Approvals does, which is where the reported
  // run was.
  const seen = await watchServer(page, 0)
  await extractBrief(page)
  await page.getByTestId('run-button').click()

  const more = page.getByTestId('more-decisions')
  let answered = 0
  for (let i = 0; i < 12; i += 1) {
    await expect(page.getByTestId('decision-prompt')).toBeVisible({ timeout: 300_000 })
    if (await more.isVisible().catch(() => false)) break
    // Clicking an option ANSWERS - `decision-submit` exists only for free-text forks and is
    // disabled until the input has content. Clicking both hung on the disabled button.
    const option = page.getByTestId('decision-option').first()
    if (await option.count()) {
      await option.click()
    } else {
      await page.getByTestId('decision-answer-input').fill('Confirmed')
      await page.getByTestId('decision-submit').click()
    }
    answered += 1
    await page.waitForTimeout(400)
  }

  // SETTLE FIRST. Answering a fork RESUMES the run, so the simulation is still building for a
  // while afterwards. The first version of this test captured a baseline 400ms after answering
  // and compared it with the state after a reload - two moments in a moving run - and reported
  // a "lost node" and a "gained fork" that were just the run getting on with it. Wait until the
  // plan stops changing, then measure.
  await settle(page)

  const openBefore = Number(await more.getAttribute('data-open-count').catch(() => '0'))
  const nodesBefore = await page.getByTestId('node-card').count()
  console.log(
    `  answered ${answered} fork(s); settled holding ${openBefore} open fork(s), ` +
      `${nodesBefore} nodes`,
  )
  test.skip(openBefore < 2, 'this brief never raises two forks at once, so nothing to check')

  const runId = new URL(page.url()).searchParams.get('run')
  expect(runId).toMatch(/^run-/)

  await page.reload()
  await expect(page.getByTestId('run-status')).toHaveText(/needs your decision/i, {
    timeout: 300_000,
  })
  await settle(page)

  const openAfter = Number(await more.getAttribute('data-open-count').catch(() => '0'))
  const nodesAfter = await page.getByTestId('node-card').count()
  console.log(
    `  after reload: ${openAfter} open fork(s), ${nodesAfter} nodes ` +
      `(was ${openBefore} and ${nodesBefore}); server re-raised ${seen.forks.size}`,
  )

  expect(openAfter, 'forks were lost across the reload').toBe(openBefore)
  expect(nodesAfter, 'the plan shrank across the reload').toBe(nodesBefore)
})
