/**
 * The canvas at scale: one copy of the graph, and a zoom floor derived from it.
 *
 * Written after a report that a large completed graph paints tiled/repeated three times at
 * extreme zoom-out, with the node count still correct.
 *
 * The repetition itself did not reproduce in this environment - not headless, and not headed
 * against the real GPU compositor. What these tests pin is the half that IS checkable, and it
 * is the half that says where the fault is not: at every zoom, from the fitted view down to the
 * floor, the DOM must hold exactly one graph. If a future change ever duplicates nodes, edges
 * or whole renderer instances, that is caught here rather than by someone noticing a strange
 * picture. It also means a repetition seen on screen while these pass is a paint artifact, not
 * duplicated data - which is the useful thing to know when chasing one.
 */

import { expect, test } from '@playwright/test'

import { completedRun } from './support'

const SHOTS = 'e2e/screenshots'

/** Everything the DOM knows about how many graphs exist, and at what scale. */
async function census(page: import('@playwright/test').Page) {
  return page.evaluate(() => {
    const viewport = document.querySelector('.react-flow__viewport') as HTMLElement | null
    const nodes = Array.from(document.querySelectorAll('.react-flow__node')) as HTMLElement[]
    const positions = nodes.map((n) => {
      const m = /translate\(([-\d.]+)px,\s*([-\d.]+)px\)/.exec(n.style.transform ?? '')
      return `${m?.[1] ?? '?'},${m?.[2] ?? '?'}`
    })
    return {
      zoom: Number(/scale\(([\d.]+)\)/.exec(viewport?.style.transform ?? '')?.[1] ?? 0),
      nodes: nodes.length,
      uniqueIds: new Set(nodes.map((n) => n.getAttribute('data-id'))).size,
      uniquePositions: new Set(positions).size,
      renderers: document.querySelectorAll('.react-flow__renderer').length,
      viewports: document.querySelectorAll('.react-flow__viewport').length,
      panes: document.querySelectorAll('.react-flow__pane').length,
      backgrounds: document.querySelectorAll('.react-flow__background').length,
      edges: document.querySelectorAll('.react-flow__edge').length,
    }
  })
}

test('the graph exists exactly once, at every zoom from fitted to the floor', async ({ page }) => {
  test.setTimeout(240_000)
  await completedRun(page)

  const fitted = await census(page)
  expect(fitted.nodes, 'the run drew no nodes').toBeGreaterThan(20)

  const seen = [{ label: 'fitted', ...fitted }]

  // Walk all the way out to the floor, checking at every step rather than only at the end -
  // the report is specifically about extreme zoom, so the extreme is where to look, but a
  // duplication that appears halfway would be just as wrong.
  for (let click = 0; click < 14; click += 1) {
    await page.locator('.react-flow__controls-zoomout').click({ timeout: 5000 }).catch(() => {})
    await page.waitForTimeout(110)
    const now = await census(page)
    if (click % 4 === 3 || click === 13) seen.push({ label: `zoom-out x${click + 1}`, ...now })
  }
  await page.waitForTimeout(500)
  const floor = await census(page)
  seen.push({ label: 'floor', ...floor })

  for (const s of seen) {
    console.log(
      `  ${s.label.padEnd(14)} zoom ${s.zoom.toFixed(4).padStart(7)}  ` +
        `nodes ${s.nodes} (${s.uniqueIds} ids, ${s.uniquePositions} positions)  ` +
        `edges ${s.edges}  renderers ${s.renderers}`,
    )

    // One instance of everything. Three painted copies with three renderers would be a
    // component bug; three painted copies with one renderer is the browser.
    expect(s.renderers, `${s.label}: more than one React Flow renderer`).toBe(1)
    expect(s.viewports, `${s.label}: more than one viewport`).toBe(1)
    expect(s.panes, `${s.label}: more than one pane`).toBe(1)
    expect(s.backgrounds, `${s.label}: more than one background layer`).toBe(1)

    // One copy of the DATA. Each of these fails differently if the graph were tripled:
    // duplicated ids, or the same ids laid out at three sets of coordinates.
    expect(s.nodes, `${s.label}: node count changed with zoom`).toBe(fitted.nodes)
    expect(s.uniqueIds, `${s.label}: duplicate node ids in the DOM`).toBe(s.nodes)
    expect(s.uniquePositions, `${s.label}: nodes stacked at repeated positions`).toBe(s.nodes)
    expect(s.edges, `${s.label}: edge count changed with zoom`).toBe(fitted.edges)
  }

  await page.screenshot({ path: `${SHOTS}/zoom-01-floor.png` })
})

test('the zoom floor is derived from the graph, not a constant', async ({ page }) => {
  test.setTimeout(240_000)
  await completedRun(page)

  const zoomNow = () =>
    page.evaluate(() => {
      const el = document.querySelector('.react-flow__viewport') as HTMLElement
      return Number(/scale\(([\d.]+)\)/.exec(el?.style.transform ?? '')?.[1] ?? 0)
    })

  // Fit the whole programme, then find the floor by clicking until it stops moving.
  await page.locator('.react-flow__controls-fitview').click()
  await page.waitForTimeout(900)
  const fitZoom = await zoomNow()
  expect(fitZoom, 'fit-view produced no zoom').toBeGreaterThan(0)

  let floor = fitZoom
  for (let i = 0; i < 25; i += 1) {
    await page.locator('.react-flow__controls-zoomout').click({ timeout: 5000 }).catch(() => {})
    await page.waitForTimeout(90)
    const now = await zoomNow()
    if (Math.abs(now - floor) < 1e-6) break
    floor = now
  }
  console.log(`  fit ${fitZoom.toFixed(4)} -> floor ${floor.toFixed(4)} (${(fitZoom / floor).toFixed(1)}x out)`)

  // Below the fit, or fitView itself gets clamped and "show me the whole programme" silently
  // returns a cropped graph — the fault the previous fixed floor of 0.15 caused.
  expect(floor, 'the floor is at or above the zoom the plan fits at').toBeLessThan(fitZoom)

  // But not so far below that the programme becomes a smudge. At the old constant of 0.02 a
  // 3,504px graph rendered 70px wide: nothing legible, nothing clickable, and the sub-pixel
  // regime where paint artifacts live.
  expect(
    fitZoom / floor,
    `the floor is ${(fitZoom / floor).toFixed(1)}x below fit — that is a smudge, not a view`,
  ).toBeLessThan(9)
  expect(fitZoom / floor, 'there is no real headroom below the fit').toBeGreaterThan(1.5)
})
