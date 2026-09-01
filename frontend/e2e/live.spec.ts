/**
 * The live path, end to end in a real browser against a real backend.
 *
 * Types a brief that is NOT the seed, extracts it, confirms it, runs the simulation over the
 * WebSocket, answers the forks it stops at, and renders the flow the backend actually built.
 *
 * Needs the API running:
 *     LLM_PROVIDER=stub python -m uvicorn backend.app.main:app --port 8000
 *
 * Skipped when the API is not reachable, so `npm run e2e` stays runnable without it. Run with
 * LLM_PROVIDER=stub for a fast, free, deterministic pass, or with the real provider to exercise
 * genuine reasoning.
 */

import { expect, test, type Page } from '@playwright/test'

const SHOTS = 'e2e/screenshots'
const API = process.env.E2E_API ?? process.env.E2E_BASE_URL ?? 'http://localhost:8000'

/** Deliberately unlike the seed: Chennai, Tier IV, 30 MW, 2N, brownfield, single handover. */
const CHENNAI_BRIEF = `We are bidding a 30 MW Tier IV hyperscale data centre in Chennai, on a
brownfield parcel inside the SIPCOT industrial park. Topology is 2N on both the electrical and
cooling trains. Scope is design-build.

Delivery: we self-perform civil and structure. Electrical and mechanical are turnkey specialist
packages. Transformers are owner-furnished by the client. Fire suppression and BMS are
subcontracted.

Power: the site has no existing feeder. We build the HT substation and the utility connection is
on our scope, which the client considers the main programme risk.

Client wants a single handover, not phased. Target ready-for-service is Q4 2027.`

test.beforeAll(async () => {
  const reachable = await fetch(`${API}/health`)
    .then((r) => r.ok)
    .catch(() => false)
  test.skip(!reachable, `planner API not reachable at ${API} — start uvicorn to run these`)
})

async function extract(page: Page) {
  await page.goto('/')
  await expect(page.getByTestId('intake-screen')).toBeVisible()
  await page.getByTestId('brief-input').fill(CHENNAI_BRIEF)
  await page.getByTestId('extract-button').click()
  await expect(page.getByTestId('confirm-screen')).toBeVisible({ timeout: 60_000 })
}

test('extracts a brief that is not the seed, and cites where each field came from', async ({
  page,
}) => {
  await extract(page)

  // The values are the Chennai brief's, not the seed's.
  await expect(page.getByTestId('field-city')).toHaveValue(/chennai/i)
  await expect(page.getByTestId('field-tier')).toHaveValue('IV')
  await expect(page.getByTestId('field-it_load_mw')).toHaveValue('30')
  await expect(page.getByTestId('field-redundancy_topology')).toHaveValue('2N')
  await expect(page.getByTestId('field-site_context')).toHaveValue(/brownfield/i)

  // Every extracted field shows the phrase it came from.
  const quotes = page.locator('.quote')
  expect(await quotes.count()).toBeGreaterThan(3)

  await page.screenshot({ path: `${SHOTS}/live-01-confirm.png` })
})

test('the app never renders the golden fixture', async ({ page }) => {
  // The seed project would be a fixture leaking into the user path.
  await page.goto('/')
  await expect(page.getByTestId('intake-screen')).toBeVisible()
  await expect(page.locator('body')).not.toContainText('POC DC')
  await expect(page.locator('body')).not.toContainText('Navi Mumbai')
})

test('runs the simulation live, stops to ask, and draws the real plan', async ({ page }) => {
  // A real provider answers each of the 13 stages in ~10-20s, and the walk pauses at every
  // genuine fork, so a full live run is minutes not seconds. Against LLM_PROVIDER=stub the same
  // test finishes in seconds.
  test.setTimeout(900_000)
  await extract(page)

  await page.getByTestId('run-button').click()
  await expect(page.getByTestId('run-panel')).toBeVisible()

  // Nodes appear from the event stream while the run is still in flight.
  await expect(page.locator('[data-testid="node-card"]').first()).toBeVisible({ timeout: 90_000 })

  let answered = 0
  for (let round = 0; round < 60; round += 1) {
    const status = page.getByTestId('run-status')
    await expect(status).not.toHaveText(/Simulating/, { timeout: 300_000 })

    if ((await status.textContent())?.includes('Simulation complete')) break
    if ((await status.textContent())?.includes('failed')) {
      throw new Error(`run failed: ${await page.getByTestId('run-error').textContent()}`)
    }

    // Stopped at a genuine fork: it must say why, and offer a way to answer.
    const prompt = page.getByTestId('decision-prompt')
    await expect(prompt).toBeVisible()
    await expect(page.getByTestId('decision-why')).not.toBeEmpty()

    if (answered === 0) {
      await page.screenshot({ path: `${SHOTS}/live-02-decision-prompt.png` })
    }

    const option = page.getByTestId('decision-option').first()
    if (await option.count()) {
      await option.click()
    } else {
      await page.getByTestId('decision-answer-input').fill('Confirmed')
      await page.getByTestId('decision-submit').click()
    }
    answered += 1
  }

  await expect(page.getByTestId('run-status')).toHaveText(/Simulation complete/, {
    timeout: 300_000,
  })
  expect(answered).toBeGreaterThan(0) // it really did stop and ask

  // The finished graph is the backend's output, and it is about THIS brief.
  await expect(page.locator('[data-testid="node-card"]').first()).toBeVisible()
  expect(await page.locator('[data-testid="node-card"]').count()).toBeGreaterThan(5)
  await expect(page.locator('.topbar')).toContainText('Chennai')
  await expect(page.locator('.topbar')).toContainText('Tier IV')
  await expect(page.getByTestId('answered-list')).toBeVisible()

  await page.screenshot({ path: `${SHOTS}/live-03-completed-flow.png` })
})
