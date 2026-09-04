/**
 * The 2D canvas must stay pannable and zoomable after a run completes.
 *
 * Reported from manual testing: once a simulation reached "complete", dragging the canvas and
 * clicking the zoom controls did nothing, while clicking a node still opened its detail panel.
 * Input was reaching the view; the viewport was not moving.
 *
 * These assertions read the VIEWPORT TRANSFORM, not the events. "The drag fired" and "the canvas
 * moved" are different claims, and only the second is what a reader needs.
 */

import { expect, test, type Page } from '@playwright/test'

import { apiReachable, completedRun } from './support'

const SHOTS = 'e2e/screenshots'

test.beforeAll(async () => {
  test.skip(!(await apiReachable()), 'no API on :8000 — start it with LLM_PROVIDER=stub')
})

interface Viewport {
  x: number
  y: number
  zoom: number
}

/** The transform React Flow has actually applied to the canvas. */
async function viewport(page: Page): Promise<Viewport> {
  return page.evaluate(() => {
    const el = document.querySelector('.react-flow__viewport') as HTMLElement
    const t = el?.style.transform ?? ''
    const translate = /translate\(([-\d.]+)px,\s*([-\d.]+)px\)/.exec(t)
    const scale = /scale\(([\d.]+)\)/.exec(t)
    return {
      x: Number(translate?.[1] ?? 0),
      y: Number(translate?.[2] ?? 0),
      zoom: Number(scale?.[1] ?? 0),
    }
  })
}

const moved = (a: Viewport, b: Viewport) =>
  Math.abs(a.x - b.x) > 5 || Math.abs(a.y - b.y) > 5

/** A point on the pane with no node under it, so a drag pans rather than moving a card. */
async function emptySpot(page: Page): Promise<{ x: number; y: number }> {
  const canvas = (await page.locator('.canvas').boundingBox())!
  const cards = await page.locator('.react-flow__node').evaluateAll((els) =>
    els.map((e) => e.getBoundingClientRect()).map((r) => ({
      l: r.left, t: r.top, r: r.right, b: r.bottom,
    })),
  )
  for (let ny = 0.2; ny <= 0.85; ny += 0.05) {
    for (let nx = 0.15; nx <= 0.85; nx += 0.05) {
      const x = canvas.x + canvas.width * nx
      const y = canvas.y + canvas.height * ny
      if (!cards.some((c) => x >= c.l - 8 && x <= c.r + 8 && y >= c.t - 8 && y <= c.b + 8)) {
        return { x, y }
      }
    }
  }
  throw new Error('no empty spot on the canvas to drag from')
}

test('the canvas can be panned after the run completes', async ({ page }) => {
  await completedRun(page)
  await expect(page.getByTestId('run-status')).toHaveText(/Simulation complete/)
  await page.waitForTimeout(1200) // let the final auto-fit settle

  const before = await viewport(page)
  await page.screenshot({ path: `${SHOTS}/17-01-before-pan.png` })

  const spot = await emptySpot(page)
  await page.mouse.move(spot.x, spot.y)
  await page.mouse.down()
  await page.mouse.move(spot.x - 220, spot.y - 130, { steps: 25 })
  await page.mouse.up()
  await page.waitForTimeout(600)

  const after = await viewport(page)
  await page.screenshot({ path: `${SHOTS}/17-02-after-pan.png` })
  console.log('pan:', JSON.stringify(before), '->', JSON.stringify(after))

  expect(
    moved(before, after),
    `the canvas did not pan: ${JSON.stringify(before)} -> ${JSON.stringify(after)}`,
  ).toBe(true)
})

test('the canvas can be zoomed after the run completes', async ({ page }) => {
  await completedRun(page)
  await expect(page.getByTestId('run-status')).toHaveText(/Simulation complete/)
  await page.waitForTimeout(1200)

  const before = await viewport(page)

  // The app's own control, which is what a reader reaches for.
  for (let i = 0; i < 3; i += 1) {
    await page.locator('.react-flow__controls-zoomout').click()
    await page.waitForTimeout(150)
  }
  await page.waitForTimeout(500)

  const after = await viewport(page)
  await page.screenshot({ path: `${SHOTS}/17-03-after-zoom.png` })
  console.log('zoom:', before.zoom, '->', after.zoom)

  expect(
    after.zoom,
    `zoom-out did nothing: still ${after.zoom} after three clicks`,
  ).toBeLessThan(before.zoom)
})

test('the fit-view control reframes the graph after completion', async ({ page }) => {
  await completedRun(page)
  await page.waitForTimeout(1200)

  // Move somewhere else first, so fit-view has something to undo.
  for (let i = 0; i < 3; i += 1) {
    await page.locator('.react-flow__controls-zoomin').click()
    await page.waitForTimeout(120)
  }
  await page.waitForTimeout(400)
  const zoomedIn = await viewport(page)

  await page.locator('.react-flow__controls-fitview').click()
  await page.waitForTimeout(700)
  const fitted = await viewport(page)
  console.log('fit:', JSON.stringify(zoomedIn), '->', JSON.stringify(fitted))

  expect(
    fitted.zoom !== zoomedIn.zoom || moved(zoomedIn, fitted),
    'fit-view changed nothing',
  ).toBe(true)
})

test('a pan is not undone a moment later', async ({ page }) => {
  // The specific way this could regress: something re-fits the view after the reader has moved
  // it, so the canvas appears to work for an instant and then snaps back.
  await completedRun(page)
  await page.waitForTimeout(1200)

  const spot = await emptySpot(page)
  await page.mouse.move(spot.x, spot.y)
  await page.mouse.down()
  await page.mouse.move(spot.x - 200, spot.y - 110, { steps: 20 })
  await page.mouse.up()
  await page.waitForTimeout(300)

  const justAfter = await viewport(page)
  await page.waitForTimeout(2500)
  const later = await viewport(page)

  expect(
    moved(justAfter, later),
    `the view moved on its own after the drag: ${JSON.stringify(justAfter)} -> ` +
      `${JSON.stringify(later)}`,
  ).toBe(false)
})

test('there is real headroom below the zoom the plan fits at', async ({ page }) => {
  // The root cause of the reported bug. A full programme fits at about 0.16, and the component
  // floor was 0.15 - one click of headroom. React Flow disables zoom-out at the floor, so the
  // control stopped responding with nothing said, and on a larger plan the same floor would
  // have clamped the auto-fit and cropped the graph.
  await completedRun(page)
  await page.waitForTimeout(1200)

  const fitted = await viewport(page)
  const floor = await page.evaluate(() => {
    // React Flow renders the pane with its configured bounds; read what the app actually set.
    const el = document.querySelector('.react-flow') as HTMLElement & { __rf?: unknown }
    return Number(el?.getAttribute('data-min-zoom') ?? 0)
  })

  // Four clicks must each still move the view. Under the old floor the second one hung on a
  // disabled button, which is how this surfaced as "zoom does nothing".
  let previous = fitted.zoom
  for (let i = 0; i < 4; i += 1) {
    await page.locator('.react-flow__controls-zoomout').click({ timeout: 5000 })
    await page.waitForTimeout(180)
    const now = (await viewport(page)).zoom
    expect(now, `zoom-out click ${i + 1} did not reduce zoom (${previous})`).toBeLessThan(previous)
    previous = now
  }
  console.log('zoom headroom:', fitted.zoom, '->', previous, 'floor attr:', floor)
})
