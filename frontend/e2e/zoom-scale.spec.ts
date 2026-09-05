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

import { completedRun, keepOnlyStages, stagesContaining } from './support'

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

  const readState = () =>
    page.evaluate(() => {
      const canvas = document.querySelector('.canvas') as HTMLElement
      const viewport = document.querySelector('.react-flow__viewport') as HTMLElement
      const nodes = Array.from(document.querySelectorAll('.react-flow__node'))
      let left = Infinity
      let top = Infinity
      let right = -Infinity
      let bottom = -Infinity
      for (const node of nodes) {
        const r = node.getBoundingClientRect()
        left = Math.min(left, r.left)
        top = Math.min(top, r.top)
        right = Math.max(right, r.right)
        bottom = Math.max(bottom, r.bottom)
      }
      const pane = canvas.getBoundingClientRect()
      return {
        // The floor the view DERIVED for this graph. Read from the app rather than inferred
        // from behaviour: see the note below on why inferring it is a trap.
        derived: Number(canvas.getAttribute('data-zoom-floor') ?? 0),
        zoom: Number(/scale\(([\d.]+)\)/.exec(viewport?.style.transform ?? '')?.[1] ?? 0),
        nodes: nodes.length,
        zoomOutDisabled:
          (document.querySelector('.react-flow__controls-zoomout') as HTMLButtonElement)
            ?.disabled ?? null,
        everythingVisible:
          nodes.length > 0 && left >= pane.left - 2 && right <= pane.right + 2 &&
          top >= pane.top - 2 && bottom <= pane.bottom + 2,
      }
    })

  const zoomOutFully = async () => {
    for (let i = 0; i < 30; i += 1) {
      const button = page.locator('.react-flow__controls-zoomout')
      if (await button.isDisabled()) break
      const before = (await readState()).zoom
      await button.click({ timeout: 4000 }).catch(() => {})
      await page.waitForTimeout(90)
      if (Math.abs((await readState()).zoom - before) < 1e-6) break
    }
    return readState()
  }

  // --- the whole programme, zoomed all the way out
  const big = await zoomOutFully()
  console.log(`  full graph: ${big.nodes} nodes, derived floor ${big.derived.toFixed(4)}, ` +
    `stopped at ${big.zoom.toFixed(4)}, zoom-out disabled: ${big.zoomOutDisabled}, ` +
    `whole graph visible: ${big.everythingVisible}`)

  // The floor is low enough to show the entire programme. This is what the derived floor is
  // for: a fixed floor above it returns a cropped graph the reader cannot zoom out of, which
  // is the fault the old constant 0.15 caused.
  expect(big.everythingVisible, 'the whole graph is not visible even at maximum zoom-out')
    .toBe(true)
  expect(big.zoomOutDisabled, 'the control is not disabled at the floor, so there is no floor')
    .toBe(true)

  // --- a much smaller graph
  const withActivities = await stagesContaining(page, 'activity')
  await keepOnlyStages(page, withActivities.slice(0, 2))
  await page.waitForTimeout(1500)
  const small = await readState()
  console.log(`  collapsed:  ${small.nodes} nodes, derived floor ${small.derived.toFixed(4)}`)

  expect(small.nodes, 'collapsing removed nothing').toBeLessThan(big.nodes)

  // A CONSTANT floor would be identical for both graphs; a derived one is higher for the
  // smaller graph, because less zooming out is needed to see all of it.
  //
  // Asserted on the DERIVED value, not on the lowest zoom the controls reach. Two earlier
  // versions of this test got that wrong in different ways - one recomputed the app's formula
  // from different inputs and disagreed with it on CI, the other measured the zoom reachable by
  // clicking. The second looks right and is not: when the floor RISES, React Flow refuses
  // further zoom-out but does not pull the current transform up to meet it, so the reachable
  // zoom is wherever the view already was. That reads as "the floor never changed" when in fact
  // it changed and is being enforced - which the disabled control below is the real evidence of.
  expect(small.derived, `the floor did not change with the graph ` +
    `(${small.derived} for ${small.nodes} nodes vs ${big.derived} for ${big.nodes}) — ` +
    'it is behaving like a constant').toBeGreaterThan(big.derived)
  expect(small.derived / big.derived, 'the floor barely moved for a much smaller graph')
    .toBeGreaterThan(1.2)
})
