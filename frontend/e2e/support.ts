/**
 * Shared helpers for the browser suite.
 *
 * Every test now drives the REAL path — brief, extraction, confirmation, a streamed simulation —
 * because that is the only path the app has. The golden SimulationOutput is a backend test
 * fixture and is no longer reachable from the UI, so a UI test has to earn its graph the same
 * way a user does.
 *
 * Run the API with LLM_PROVIDER=stub to keep this fast, free and deterministic:
 *     LLM_PROVIDER=stub python -m uvicorn backend.app.main:app --port 8000
 */

import { expect, type Page } from '@playwright/test'

/**
 * Where the tests probe for a live API.
 *
 * Separate from the browser's own API base: the reachability check runs in Node, so it needs an
 * absolute URL even when the app itself calls its own origin. Point E2E_BASE_URL and E2E_API at
 * a deployment to run this suite against it.
 */
export const API = process.env.E2E_API ?? process.env.E2E_BASE_URL ?? 'http://localhost:8000'

/** Deliberately unlike the seed: Chennai, Tier IV, 30 MW, 2N, brownfield, single handover. */
export const CHENNAI_BRIEF = `We are bidding a 30 MW Tier IV hyperscale data centre in Chennai, on a
brownfield parcel inside the SIPCOT industrial park. Topology is 2N on both the electrical and
cooling trains. Scope is design-build.

Delivery: we self-perform civil and structure. Electrical and mechanical are turnkey specialist
packages. Transformers are owner-furnished by the client. Fire suppression and BMS are
subcontracted.

Power: the site has no existing feeder. We build the HT substation and the utility connection is
on our scope, which the client considers the main programme risk.

Client wants a single handover, not phased. Target ready-for-service is Q4 2027.`

export async function apiReachable(): Promise<boolean> {
  return fetch(`${API}/health`)
    .then((r) => r.ok)
    .catch(() => false)
}

/** Brief -> extraction -> confirmation. Leaves the app on the confirm screen. */
export async function extractBrief(page: Page, brief = CHENNAI_BRIEF): Promise<void> {
  await page.goto('/')
  await expect(page.getByTestId('intake-screen')).toBeVisible()
  await page.getByTestId('brief-input').fill(brief)
  await page.getByTestId('extract-button').click()
  await expect(page.getByTestId('confirm-screen')).toBeVisible({ timeout: 120_000 })
}

/**
 * Drive a whole simulation, answering every fork it stops at. Returns how many it asked.
 *
 * Answering the first offered option is fine here: the point under test is that the run stops,
 * explains itself, and resumes — not which answer a planner would choose.
 */
export async function runToCompletion(page: Page, maxRounds = 80): Promise<number> {
  await page.getByTestId('run-button').click()
  await expect(page.getByTestId('run-panel')).toBeVisible()

  let answered = 0
  for (let round = 0; round < maxRounds; round += 1) {
    const status = page.getByTestId('run-status')
    await expect(status).not.toHaveText(/Simulating/, { timeout: 300_000 })

    const text = (await status.textContent()) ?? ''
    if (text.includes('Simulation complete')) return answered
    if (text.includes('failed')) {
      throw new Error(`run failed: ${await page.getByTestId('run-error').textContent()}`)
    }

    await expect(page.getByTestId('decision-prompt')).toBeVisible()
    const option = page.getByTestId('decision-option').first()
    if (await option.count()) {
      await option.click()
    } else {
      await page.getByTestId('decision-answer-input').fill('Confirmed')
      await page.getByTestId('decision-submit').click()
    }
    answered += 1
  }
  throw new Error(`simulation did not finish within ${maxRounds} decision rounds`)
}

/** A completed run, ready for the view assertions. */
export async function completedRun(page: Page): Promise<void> {
  await extractBrief(page)
  await runToCompletion(page)
  await expect(page.getByTestId('run-status')).toHaveText(/Simulation complete/)
  // `attached`, not `visible`: a large graph does not fit the viewport at any sensible zoom, so
  // the first node in DOM order may legitimately be off-screen. Waiting for visibility there
  // makes the wait depend on layout luck rather than on the run having finished.
  await page.waitForSelector('[data-testid="node-card"]', { state: 'attached' })
  await page.waitForTimeout(700) // let the final fit settle before measuring or shooting
}


/**
 * Collapse stages until the remaining graph fits on screen.
 *
 * A full walk is 13 stage columns wide, which does not fit any viewport at a legible zoom - so
 * some nodes are legitimately off-screen. Collapsing is the view's own answer to that
 * (VISUALIZATION_SPEC.md section 1: never render a large graph flat), and it is what a planner
 * would do before working on one part of the programme.
 */
export async function stagesContaining(page: Page, kind: string): Promise<string[]> {
  return page.$$eval(
    `[data-testid="node-card"][data-kind="${kind}"]`,
    (els) => Array.from(new Set(els.map((e) => e.getAttribute('data-stage')!))),
  )
}

/**
 * Collapse every stage except the named ones.
 *
 * A full walk is 13 columns wide and does not fit any viewport at a legible zoom, so some nodes
 * are legitimately off-screen. Collapsing is the view's own answer (VISUALIZATION_SPEC.md
 * section 1: never render a large graph flat) and is what a planner does before working on one
 * part of the programme.
 *
 * Keep stages by NAME rather than by position: several stages - design, approvals, procurement -
 * have no fragnets in the library yet, so "the first three" can contain no activities at all.
 */
export async function keepOnlyStages(page: Page, keep: string[]): Promise<void> {
  const section = page.locator('.sidebar section').filter({ hasText: 'Stages' })
  const buttons = section.locator('button')
  const total = await buttons.count()

  for (let i = 0; i < total; i += 1) {
    const button = buttons.nth(i)
    const text = ((await button.textContent()) ?? '').trim()
    // textContent runs the label and the count together ("substructure10"), so strip trailing
    // digits without expecting a separator - requiring one silently hid every stage.
    const stage = text.replace(/\d+$/, '').trim().replace(/ /g, '_')
    if (!keep.includes(stage)) await button.click()
  }
  await page.waitForTimeout(500)
  await page.locator('.react-flow__controls-fitview').click()
  await page.waitForTimeout(400)
}

/**
 * Hover an activity card that is actually inside the canvas, and return its label.
 *
 * Picking a node by name assumed a fixture. With a live run the graph varies, and a node may sit
 * outside the viewport - so choose one that is on screen, and let the assertions be about the
 * highlight behaviour rather than about which node was hovered.
 */
export async function hoverAnActivity(page: Page): Promise<string> {
  const canvas = (await page.locator('.canvas').boundingBox())!
  const cards = page.locator('.react-flow__node').filter({
    has: page.locator('[data-kind="activity"]'),
  })

  const count = await cards.count()
  for (let i = 0; i < count; i += 1) {
    const card = cards.nth(i)
    const box = await card.boundingBox()
    if (!box) continue
    const cx = box.x + box.width / 2
    const cy = box.y + box.height / 2
    const inside =
      cx > canvas.x + 20 &&
      cx < canvas.x + canvas.width - 20 &&
      cy > canvas.y + 20 &&
      cy < canvas.y + canvas.height - 20
    if (!inside) continue

    for (let attempt = 0; attempt < 3; attempt += 1) {
      await page.mouse.move(canvas.x + 5, canvas.y + 5)
      await page.waitForTimeout(60)
      await page.mouse.move(cx, cy, { steps: 12 })
      await page.mouse.move(cx + 3, cy + 3)
      await page.waitForTimeout(250)
      if (await page.locator('[data-highlighted="true"]').count()) {
        return (await card.locator('[data-testid="node-label"]').textContent()) ?? ''
      }
    }
  }
  throw new Error('no on-screen activity card could be hovered')
}
