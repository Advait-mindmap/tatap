/**
 * Are the decision points reachable when a run finishes?
 *
 * Stop-and-ask is the product's differentiator (CLAUDE.md rule 3). A plan whose decision points
 * are off-screen, or too small to read, buries the one thing that makes this more than a
 * Gantt generator. Six UI tests are marked fixme because cards sit off-screen at the opening
 * zoom on a CI runner — the same layout a user gets, so this asks whether that is a test
 * artefact or a real UX fault.
 *
 * This measures rather than asserts a preference: how many decision points exist, how many are
 * actually inside the canvas, what zoom the view settled at, and how big a card is on screen.
 */

import { expect, test, type Page } from '@playwright/test'

import { apiReachable, completedRun } from './support'

const SHOTS = 'e2e/screenshots'

test.beforeAll(async () => {
  test.skip(!(await apiReachable()), 'no API on :8000 — start it with LLM_PROVIDER=stub')
})

interface Reachability {
  zoom: number
  total: number
  onScreen: number
  offScreen: number
  cardWidthPx: number
  labelFontPx: number
  graphWidthPx: number
  canvasWidthPx: number
}

async function measure(page: Page, kind: string): Promise<Reachability> {
  return page.evaluate((nodeKind) => {
    const viewport = document.querySelector('.react-flow__viewport') as HTMLElement
    const scale = Number(/scale\(([\d.]+)\)/.exec(viewport.style.transform)?.[1] ?? 0)
    const canvas = document.querySelector('.canvas')!.getBoundingClientRect()

    const cards = Array.from(
      document.querySelectorAll(`[data-testid="node-card"][data-kind="${nodeKind}"]`),
    ) as HTMLElement[]

    let onScreen = 0
    let width = 0
    let fontPx = 0
    for (const card of cards) {
      const r = card.getBoundingClientRect()
      if (r.width === 0 || r.height === 0) continue // unmeasured by React Flow
      width = Math.max(width, r.width)
      const label = card.querySelector('[data-testid="node-label"]')
      if (label) fontPx = Math.max(fontPx, parseFloat(getComputedStyle(label).fontSize) * scale)
      const inside =
        r.left >= canvas.left && r.right <= canvas.right &&
        r.top >= canvas.top && r.bottom <= canvas.bottom
      if (inside) onScreen += 1
    }

    const all = Array.from(document.querySelectorAll('.react-flow__node')) as HTMLElement[]
    const boxes = all.map((e) => e.getBoundingClientRect()).filter((r) => r.width > 0)
    const graphWidth = boxes.length
      ? Math.max(...boxes.map((r) => r.right)) - Math.min(...boxes.map((r) => r.left))
      : 0

    return {
      zoom: scale,
      total: cards.length,
      onScreen,
      offScreen: cards.length - onScreen,
      cardWidthPx: Math.round(width),
      labelFontPx: Math.round(fontPx * 10) / 10,
      graphWidthPx: Math.round(graphWidth),
      canvasWidthPx: Math.round(canvas.width),
    }
  }, kind)
}

test('a finished run puts its decision points where the reader can find them', async ({ page }) => {
  await completedRun(page)
  await expect(page.getByTestId('run-status')).toHaveText(/Simulation complete/)

  await page.screenshot({ path: `${SHOTS}/ux-01-default-zoom.png` })

  const forks = await measure(page, 'decision_point')
  const activities = await measure(page, 'activity')
  console.log('decision points:', JSON.stringify(forks, null, 2))
  console.log('activities     :', JSON.stringify(activities, null, 2))

  expect(forks.total, 'the run raised no decision points to look for').toBeGreaterThan(0)

  // The differentiator has to be on screen when the run ends. Not all of them — but a reader
  // who has just watched the plan build should see the questions it stopped on without hunting.
  expect(
    forks.onScreen,
    `all ${forks.total} decision points are off-screen at the opening zoom ` +
      `(zoom ${forks.zoom}, graph ${forks.graphWidthPx}px in a ${forks.canvasWidthPx}px canvas)`,
  ).toBeGreaterThan(0)

  // The whole programme must be on screen, not one card in a corner of it. Before this was
  // fixed the view sat at zoom 1.0 on a 3742px graph in a 666px canvas, showing a single node.
  expect(
    forks.zoom,
    `the view never fitted the plan (zoom ${forks.zoom} on a ${forks.graphWidthPx}px graph ` +
      `in a ${forks.canvasWidthPx}px canvas)`,
  ).toBeLessThan(0.9)
  expect(activities.onScreen, 'not one activity is on screen').toBeGreaterThan(0)
})

/**
 * Wait for the canvas transform to stop moving.
 *
 * The stepper animates - `setCenter(..., { zoom: 0.9, duration: 400 })` - so every measurement
 * taken after it is a measurement of an animation in progress unless something waits for the
 * animation to END. A fixed `waitForTimeout` is not that something: it encodes how long the
 * animation took on the machine that wrote the test. This is what failed on CI, reporting zoom
 * 0.13058 - the pre-click fitted zoom, measured before the stepper had moved anything at all.
 *
 * Sampling is tied to the page's own animation frames rather than to the test's clock, so a
 * runner producing frames slowly is merely slow here instead of wrong. The condition is that the
 * transform has MOVED from where it was and then held still for two consecutive frames - not that
 * it reached any particular zoom, which would make the assertions that follow vacuous.
 */
async function settleView(page: Page, from: number): Promise<void> {
  await page
    .waitForFunction(
      (previous) => {
        const viewport = document.querySelector('.react-flow__viewport') as HTMLElement | null
        const zoom = Number(/scale\(([\d.]+)\)/.exec(viewport?.style.transform ?? '')?.[1] ?? 0)
        const store = window as unknown as { __lastZoom?: number }
        const held = store.__lastZoom !== undefined && Math.abs(store.__lastZoom - zoom) < 1e-6
        store.__lastZoom = zoom
        return zoom > 0 && Math.abs(zoom - previous) > 1e-6 && held
      },
      from,
      { timeout: 15_000, polling: 'raf' },
    )
    .catch(() => {})
  // A settle that timed out leaves the assertions to report what the view actually holds; it
  // never asserts success on its own.
  await page.evaluate(() => {
    delete (window as unknown as { __lastZoom?: number }).__lastZoom
  })
}

test('the decision stepper centres a fork at a readable zoom', async ({ page }) => {
  // Fitting a thirteen-stage programme is never legible — every fork is a two-pixel smudge.
  // So "reachable" cannot mean "visible in the overview"; it means one click away.
  await completedRun(page)

  const stepper = page.getByTestId('focus-decision-button')
  await expect(stepper).toBeVisible()

  const settledAt = (await measure(page, 'decision_point')).zoom
  await stepper.click()
  await settleView(page, settledAt)

  const forks = await measure(page, 'decision_point')
  console.log('after focusing a decision:', JSON.stringify(forks, null, 2))

  expect(forks.onScreen, 'focusing brought no decision point on screen').toBeGreaterThan(0)

  // Assert the ZOOM, which is what the control sets and is the same on every machine. The
  // rendered font size follows from it; asserting that alone was a platform trap - the stepper
  // used fitView, whose zoom is derived from bounds and pane size, and the same click gave 0.75
  // locally and about 0.5 on a CI runner, i.e. 6.2px labels.
  expect(
    forks.zoom,
    `the stepper left the view at zoom ${forks.zoom}, too far out to read`,
  ).toBeGreaterThanOrEqual(0.75)
  expect(
    forks.labelFontPx,
    `the focused decision renders at ${forks.labelFontPx}px — still unreadable`,
  ).toBeGreaterThanOrEqual(8)

  await expect(page.getByTestId('trail-panel')).toBeVisible()

  // AND IT STAYS THERE. The automatic fit records a signature only when `fitView` succeeds, and
  // it fails for the first frames after nodes land - so a fit could in principle still be
  // outstanding when the reader clicks, fire when React Flow finishes attaching, and throw the
  // view back to the whole-programme zoom.
  //
  // A dwell rather than a wait-for-condition, deliberately: the property under test is that
  // nothing happens, and there is no event for the absence of one. This is the one shape of
  // fixed wait that is not a timing defect - it can report a false pass on a slow machine, never
  // a false failure.
  //
  // FIVE SECONDS IS A MEASURED NUMBER, NOT A ROUND ONE. Under a 20x CPU throttle - the condition
  // where a late fit would land latest, and so where too short a dwell would miss it - a view
  // change takes up to 2.0s to finish rendering after its trigger. Five seconds covers a refit
  // triggered on the click plus that long again to land. It started at 2500ms, which the same
  // measurement showed to be marginal.
  await page.waitForTimeout(5000)
  const held = await measure(page, 'decision_point')
  expect(
    held.zoom,
    `the view was refitted after the reader focused a fork (0.9 -> ${held.zoom})`,
  ).toBeGreaterThanOrEqual(0.75)

  await page.screenshot({ path: `${SHOTS}/ux-02-decision-focused.png` })
})

test('stepping again moves to a different decision point', async ({ page }) => {
  await completedRun(page)
  const stepper = page.getByTestId('focus-decision-button')

  const fitted = (await measure(page, 'decision_point')).zoom
  await stepper.click()
  await settleView(page, fitted)
  const first = await page.getByTestId('trail-panel').textContent()

  // The second step moves the CENTRE at the same zoom, so the trail panel is what changes, not
  // the scale. Wait for the panel itself to differ rather than for a duration to elapse.
  await stepper.click()
  await expect
    .poll(() => page.getByTestId('trail-panel').textContent(), { timeout: 15_000 })
    .not.toBe(first)
  const second = await page.getByTestId('trail-panel').textContent()

  expect(second, 'the stepper stayed on the same fork').not.toBe(first)
})
