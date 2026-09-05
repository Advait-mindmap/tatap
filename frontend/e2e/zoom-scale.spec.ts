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

  // The zoom at which the WHOLE graph fits the pane, computed the way FlowView computes it.
  //
  // Not the fit-view control's zoom, which is what an earlier version of this test used. That
  // control applies a 0.62 legibility floor, so on a large plan it returns ~0.59 while the whole
  // graph fits at ~0.27 - a different quantity entirely. The ratio it produced came out at 8.9
  // against a threshold of 9: passing, but by 1.2%, and measuring the wrong thing to get there.
  const geometry = await page.evaluate(() => {
    const pane = document.querySelector('.canvas')!.getBoundingClientRect()
    const nodes = Array.from(document.querySelectorAll('.react-flow__node')) as HTMLElement[]
    let minX = Infinity
    let minY = Infinity
    let maxX = -Infinity
    let maxY = -Infinity
    for (const node of nodes) {
      const m = /translate\(([-\d.]+)px,\s*([-\d.]+)px\)/.exec(node.style.transform ?? '')
      if (!m) continue
      const x = Number(m[1])
      const y = Number(m[2])
      minX = Math.min(minX, x)
      minY = Math.min(minY, y)
      maxX = Math.max(maxX, x + (node.offsetWidth || 292))
      maxY = Math.max(maxY, y + (node.offsetHeight || 104))
    }
    const width = maxX - minX
    const height = maxY - minY
    return { fits: Math.min(pane.width / width, pane.height / height), width, height }
  })
  expect(geometry.fits, 'could not measure the graph').toBeGreaterThan(0)

  // Find the floor by zooming out until it stops moving.
  let floor = await zoomNow()
  for (let i = 0; i < 30; i += 1) {
    await page.locator('.react-flow__controls-zoomout').click({ timeout: 5000 }).catch(() => {})
    await page.waitForTimeout(90)
    const now = await zoomNow()
    if (Math.abs(now - floor) < 1e-6) break
    floor = now
  }

  // The rule FlowView states: a quarter of the zoom the graph fits at, clamped to [0.005, 0.2].
  const expected = Math.min(0.2, Math.max(0.005, geometry.fits / 4))
  console.log(
    `  graph ${Math.round(geometry.width)}x${Math.round(geometry.height)}px  ` +
      `fits at ${geometry.fits.toFixed(4)}  floor ${floor.toFixed(4)}  ` +
      `expected ${expected.toFixed(4)}  (${(geometry.fits / floor).toFixed(1)}x out)`,
  )

  // Asserted against the rule rather than against a hand-picked ratio, so it stays true as the
  // plan grows. The tolerance covers node measurement differing slightly from React Flow's own.
  expect(Math.abs(floor - expected) / expected, `floor ${floor}, expected ~${expected}`)
    .toBeLessThan(0.15)

  // And the two properties the rule exists for, stated directly:
  //  - below the whole-graph fit, so fitView is never clamped and "show me the whole
  //    programme" cannot silently return a cropped graph (the fault the fixed 0.15 floor had);
  //  - real headroom, so there is somewhere to go for orientation.
  expect(floor, 'the floor is at or above the zoom the whole graph fits at')
    .toBeLessThan(geometry.fits)
  expect(geometry.fits / floor, 'there is no real headroom below the fit').toBeGreaterThan(1.5)
})
