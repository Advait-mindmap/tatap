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

test('the decision stepper centres a fork at a readable zoom', async ({ page }) => {
  // Fitting a thirteen-stage programme is never legible — every fork is a two-pixel smudge.
  // So "reachable" cannot mean "visible in the overview"; it means one click away.
  await completedRun(page)

  const stepper = page.getByTestId('focus-decision-button')
  await expect(stepper).toBeVisible()
  await stepper.click()
  await page.waitForTimeout(800) // the fit is animated

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
  await page.screenshot({ path: `${SHOTS}/ux-02-decision-focused.png` })
})

test('stepping again moves to a different decision point', async ({ page }) => {
  await completedRun(page)
  const stepper = page.getByTestId('focus-decision-button')

  await stepper.click()
  await page.waitForTimeout(700)
  const first = await page.getByTestId('trail-panel').textContent()

  await stepper.click()
  await page.waitForTimeout(700)
  const second = await page.getByTestId('trail-panel').textContent()

  expect(second, 'the stepper stayed on the same fork').not.toBe(first)
})
