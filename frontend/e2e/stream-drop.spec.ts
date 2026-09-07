/**
 * A dropped event stream must never leave the UI saying "Simulating…" forever.
 *
 * Reported from a real run (run-224ceac6444d4c0dabcd4cc15628dfd3): the client sat on
 * "Simulating…" for several minutes with node and event counts frozen. No JS errors, no failed
 * network requests. A reload forced a reattach and immediately showed the truth — "Stopped —
 * needs your decision", a valid decision card, and a node count that did not match what the
 * stuck client had been showing. The server had been fine the whole time.
 *
 * THE CAUSE, and why nothing looked wrong: a WebSocket that closes CLEANLY fires `close` and
 * NOT `error`. `runSimulation` accepted an `onClose` handler and neither caller passed one, so a
 * clean close was silently discarded and the run stayed in `running` forever. Clean closes are
 * routine — a proxy trims an idle socket, and this stream goes quiet for long stretches while a
 * stage reasons (measured p90 11.9s, max 137s between events).
 *
 * HOW THIS REPRODUCES IT, and why the obvious version does not. Cutting the socket at an
 * arbitrary moment is not enough: against the stub the run halts almost immediately, the client
 * receives `simulation_halted` before the cut lands, and the UI recovers on its own. The first
 * version of this test passed against the unfixed code for exactly that reason. What has to be
 * modelled is the reported condition — the client never learns the run stopped — so the halt
 * event is SWALLOWED and the socket then closed cleanly behind it.
 *
 * This is a worse failure than the stale-card bug fixed earlier. A stale card is wrong but
 * visible, so a reader notices and works around it. A permanent spinner offers nothing to
 * notice: the product looks like it is thinking.
 */

import { expect, test } from '@playwright/test'
import { apiReachable, extractBrief } from './support'

test.beforeAll(async () => {
  test.skip(!(await apiReachable()), 'planner API not reachable - start uvicorn to run these')
})

/** Forward everything except the halt, then close cleanly - the reported failure exactly. */
async function swallowTheHalt(page: import('@playwright/test').Page, closeCode: number) {
  const state = { swallowed: false }
  await page.routeWebSocket(/\/ws\/simulate/, (ws) => {
    const server = ws.connectToServer()
    ws.onMessage((message) => server.send(message))
    server.onMessage((message) => {
      const text = String(message)
      if (!state.swallowed && text.includes('"simulation_halted"')) {
        // The client never sees it, and the connection then goes - which is what a proxy
        // trimming an idle socket looks like from here.
        state.swallowed = true
        ws.close({ code: closeCode, reason: 'idle timeout' })
        return
      }
      ws.send(message)
    })
  })
  return state
}

test('a swallowed halt does not leave the run stuck on "Simulating…"', async ({ page }) => {
  test.setTimeout(600_000)

  const state = await swallowTheHalt(page, 1000)

  await extractBrief(page)
  await page.getByTestId('run-button').click()
  await expect(page.getByTestId('run-panel')).toBeVisible()

  await expect
    .poll(() => state.swallowed, { timeout: 300_000, message: 'the run never halted' })
    .toBe(true)

  const status = page.getByTestId('run-status')
  await expect(
    status,
    'the client sat on "Simulating…" after the halt was lost - the reported failure',
  ).not.toHaveText(/Simulating/, { timeout: 120_000 })

  const text = (await status.textContent()) ?? ''
  console.log(`  status after a swallowed halt: ${text.trim()}`)

  // Recovering means showing the TRUTH - the run halted and wants an answer - or saying the
  // connection went. Silence is the bug.
  expect(text).toMatch(/decision|complete|stopped|connection|error/i)
})

test('recovery shows the decision the run actually stopped on', async ({ page }) => {
  test.setTimeout(600_000)

  const state = await swallowTheHalt(page, 1006)

  await extractBrief(page)
  await page.getByTestId('run-button').click()
  await expect.poll(() => state.swallowed, { timeout: 300_000 }).toBe(true)

  // Not just "not stuck": the fork must be answerable, which is the whole point of noticing.
  // Reattach replays the open decisions, so the card comes back with its options.
  const prompt = page.getByTestId('decision-prompt')
  await expect(prompt, 'recovered without showing the fork the run is waiting on').toBeVisible({
    timeout: 120_000,
  })
  const options = await page.getByTestId('decision-option').count()
  console.log(`  recovered card with ${options} answerable option(s)`)
  expect(options).toBeGreaterThan(0)

  // And the STATUS has to agree with the card. Without this the test passes on the unfixed
  // build: the card renders from `decision_needed`, which arrives separately and was never
  // lost, so a reader saw an answerable fork underneath a banner still claiming the run was
  // thinking. Half-recovered is its own kind of misleading.
  await expect(page.getByTestId('run-status')).not.toHaveText(/Simulating/, { timeout: 120_000 })
})

test('the run id survives a dropped stream, so the run can be recovered by URL', async ({
  page,
}) => {
  test.setTimeout(600_000)

  const state = await swallowTheHalt(page, 1006)
  await extractBrief(page)
  await page.getByTestId('run-button').click()
  await expect.poll(() => state.swallowed, { timeout: 300_000 }).toBe(true)
  await page.waitForTimeout(2_000)

  // The address bar is the manual recovery path, and it must survive the drop that makes
  // recovery necessary.
  expect(page.url()).toMatch(/[?&]run=run-/)
})
