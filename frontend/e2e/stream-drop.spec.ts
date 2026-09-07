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
 * This is a worse failure than the stale-card bug fixed earlier. A stale card is wrong but
 * visible, so a reader notices and works around it. A permanent spinner offers nothing to
 * notice: the product looks like it is thinking.
 */

import { expect, test } from '@playwright/test'
import { apiReachable, extractBrief } from './support'

test.beforeAll(async () => {
  test.skip(!(await apiReachable()), 'planner API not reachable - start uvicorn to run these')
})

test('a cleanly dropped stream does not leave the run stuck on "Simulating…"', async ({
  page,
}) => {
  test.setTimeout(600_000)

  // Let the run start normally, then cut the stream the way a proxy would: close it, cleanly,
  // with no error. Everything before the cut must still have been delivered, so the client is
  // mid-run rather than never-started.
  let cut = false
  await page.routeWebSocket(/\/ws\/simulate/, (ws) => {
    const server = ws.connectToServer()
    ws.onMessage((message) => server.send(message))
    server.onMessage((message) => {
      ws.send(message)
      const text = String(message)
      // Cut once the run is genuinely under way - after the first stage has begun - so this
      // reproduces a drop mid-simulation rather than a failure to connect.
      if (!cut && text.includes('stage_started')) {
        cut = true
        setTimeout(() => ws.close({ code: 1000, reason: 'proxy idle timeout' }), 1500)
      }
    })
  })

  await extractBrief(page)
  await page.getByTestId('run-button').click()
  await expect(page.getByTestId('run-panel')).toBeVisible()

  // Wait for the cut to have happened and the client to have had time to notice.
  await expect.poll(() => cut, { timeout: 300_000 }).toBe(true)

  const status = page.getByTestId('run-status')
  await expect(
    status,
    'the client sat on "Simulating…" after the stream closed — the exact reported failure',
  ).not.toHaveText(/Simulating/, { timeout: 60_000 })

  const text = (await status.textContent()) ?? ''
  console.log(`  status after a clean close: ${text.trim()}`)

  // Whatever it recovers to, the user must be able to SEE that something happened. Either it
  // reattached and is showing real state, or it says the connection went. Silence is the bug.
  expect(text).toMatch(/decision|complete|stopped|connection|reconnect|error/i)
})

test('the run id survives a dropped stream, so the run can be recovered', async ({ page }) => {
  test.setTimeout(600_000)

  let cut = false
  await page.routeWebSocket(/\/ws\/simulate/, (ws) => {
    const server = ws.connectToServer()
    ws.onMessage((message) => server.send(message))
    server.onMessage((message) => {
      ws.send(message)
      if (!cut && String(message).includes('stage_started')) {
        cut = true
        setTimeout(() => ws.close({ code: 1006, reason: 'abnormal' }), 1500)
      }
    })
  })

  await extractBrief(page)
  await page.getByTestId('run-button').click()
  await expect.poll(() => cut, { timeout: 300_000 }).toBe(true)
  await page.waitForTimeout(3_000)

  // The address bar is the recovery path, and it must survive the drop that makes recovery
  // necessary.
  expect(page.url()).toMatch(/[?&]run=run-/)
})
