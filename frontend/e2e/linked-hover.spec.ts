/**
 * Task 15: linked hover between the 2D flow and the 3D model.
 *
 * VISUALIZATION_SPEC.md sections 2-3: resting the cursor on a zone in 3D (a raycast pick)
 * highlights that zone's activities in the 2D view, and hovering a 2D node highlights its zone
 * in 3D. One shared `highlight(ref)` state, two views reading it.
 *
 * Both directions are tested because they are different code paths: one starts from a mesh the
 * raycaster hits, the other from a DOM node React Flow reports. A test that only drove 2D->3D
 * would leave the pick untested, and the pick is the harder half.
 *
 * Run against the stub API so the run is fast, free and deterministic:
 *     LLM_PROVIDER=stub python -m uvicorn backend.app.main:app --port 8000
 */

import { expect, test, type Page } from '@playwright/test'

import { apiReachable, completedRun } from './support'

const SHOTS = 'e2e/screenshots'

test.beforeAll(async () => {
  test.skip(!(await apiReachable()), 'no API on :8000 — start it with LLM_PROVIDER=stub')
})

/** Finish a run and open the linked view, where both projections are on screen at once. */
async function openLinked(page: Page): Promise<void> {
  await completedRun(page)
  await page.getByTestId('view-split-button').click()
  await expect(page.getByTestId('view-split')).toBeVisible()
  await expect(page.locator('canvas')).toBeVisible()
  await page.waitForTimeout(1400) // the 3D scene paints and the 2D graph fits
}

/** The shared highlight, read off the app root rather than out of React state. */
const linkedZone = async (page: Page): Promise<string> =>
  (await page.locator('.app[data-linked-zone]').first().getAttribute('data-linked-zone')) ?? ''

/**
 * Sweep the cursor across the 3D canvas until the raycaster picks a zone.
 *
 * Which screen pixel covers which box depends on the camera, the plan's size and the pane's
 * aspect - none of which a test should hard-code. Sweeping asks the real question ("is there a
 * pickable zone under the model?") without pretending to know where it is.
 */
async function pickAZoneIn3D(page: Page, needsWork = false): Promise<string> {
  const canvas = (await page.locator('canvas').boundingBox())!
  const tried = new Set<string>()
  for (let ny = 0.3; ny <= 0.75; ny += 0.06) {
    for (let nx = 0.25; nx <= 0.8; nx += 0.05) {
      await page.mouse.move(canvas.x + canvas.width * nx, canvas.y + canvas.height * ny)
      await page.waitForTimeout(70)
      const zone = await linkedZone(page)
      if (!zone) continue
      if (!needsWork) return zone
      tried.add(zone)
      // Most zones have no zone-tagged activities, so a pick on one lights nothing in 2D by
      // design. Keep sweeping for one that does rather than asserting against a zone the plan
      // has no work for.
      if (!(await page.getByTestId('linked-zone-no-work').count())) return zone
    }
  }
  console.log('zones picked but none had activities:', [...tried].join(', '))
  return ''
}

test('hovering a zone in 3D highlights its activities in the 2D flow', async ({ page }) => {
  await openLinked(page)

  const zone = await pickAZoneIn3D(page, true)
  expect(zone, 'no pickable zone anywhere on the canvas has activities to light').not.toBe('')

  // The 3D view names what it picked.
  await expect(page.getByTestId('linked-zone-label')).toBeVisible()

  // And the 2D view lights the work that builds it, dimming the rest. Read the split's own 2D
  // pane so a stray card elsewhere cannot satisfy the assertion.
  const lit = page.locator(`.view-split-2d [data-testid="node-card"][data-highlighted="true"]`)
  const dimmed = page.locator('.view-split-2d [data-testid="node-card"][data-dimmed="true"]')

  await expect
    .poll(async () => lit.count(), { timeout: 8000 })
    .toBeGreaterThan(0)
  expect(await dimmed.count(), 'nothing was dimmed, so nothing was singled out')
    .toBeGreaterThan(0)

  // Everything lit belongs to the picked zone — that is what "linked" means.
  const zonesLit = await lit.evaluateAll((els) =>
    Array.from(new Set(els.map((e) => e.getAttribute('data-zone')))),
  )
  expect(zonesLit, `lit cards belong to ${zonesLit.join(', ')}, not just ${zone}`).toEqual([zone])

  console.log('3D -> 2D:', zone, `${await lit.count()} lit, ${await dimmed.count()} dimmed`)
  await page.screenshot({ path: `${SHOTS}/15-01-3d-to-2d.png` })
})

test('hovering a 2D node highlights its zone in the 3D model', async ({ page }) => {
  await openLinked(page)

  // Pick a card that actually belongs to a zone. Approvals and procurement work is real but
  // builds no geometry, so those nodes have nothing to point at in 3D.
  const zoned = page.locator('.view-split-2d [data-testid="node-card"][data-zone]:not([data-zone=""])')
  await expect
    .poll(async () => zoned.count(), { timeout: 8000 })
    .toBeGreaterThan(0)

  const count = await zoned.count()
  let hovered = ''
  for (let i = 0; i < count && !hovered; i += 1) {
    const card = zoned.nth(i)
    const box = await card.boundingBox()
    if (!box) continue
    // A real crossing, not a teleport: React's synthetic mouseenter is built on one.
    await page.mouse.move(8, 8)
    await page.waitForTimeout(50)
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 12 })
    await page.mouse.move(box.x + box.width / 2 + 3, box.y + box.height / 2 + 3)
    await page.waitForTimeout(250)
    hovered = await linkedZone(page)
  }

  expect(hovered, 'hovering a zoned node set no shared highlight').not.toBe('')

  // The 3D model says which zone it is showing.
  const label = page.getByTestId('linked-zone-label')
  await expect(label).toBeVisible()

  console.log('2D -> 3D:', hovered, '->', await label.textContent())
  await page.screenshot({ path: `${SHOTS}/15-02-2d-to-3d.png` })
})

test('a zone with no activities says so instead of greying the whole plan', async ({ page }) => {
  // Most zones have no zone-tagged activities. Passing such a pick straight through dimmed
  // every card and lit none: a uniformly grey plan with no explanation, which reads as a broken
  // link rather than a gap in the data.
  await openLinked(page)

  const canvas = (await page.locator('canvas').boundingBox())!
  let found = false
  for (let ny = 0.3; ny <= 0.75 && !found; ny += 0.06) {
    for (let nx = 0.25; nx <= 0.8 && !found; nx += 0.05) {
      await page.mouse.move(canvas.x + canvas.width * nx, canvas.y + canvas.height * ny)
      await page.waitForTimeout(70)
      if (await page.getByTestId('linked-zone-no-work').count()) found = true
    }
  }
  test.skip(!found, 'every pickable zone has activities on this plan')

  await expect(page.getByTestId('linked-zone-no-work')).toBeVisible()
  const dimmed = page.locator('.view-split-2d [data-testid="node-card"][data-dimmed="true"]')
  expect(await dimmed.count(), 'the whole plan was greyed for a zone with no work').toBe(0)
})

test('the highlight clears when the cursor leaves', async ({ page }) => {
  // A highlight that sticks is worse than none: the reader ends up looking at a plan filtered
  // by something they have forgotten they hovered.
  await openLinked(page)

  const zone = await pickAZoneIn3D(page)
  test.skip(zone === '', 'no zone could be picked, covered by the first test')

  const canvas = (await page.locator('canvas').boundingBox())!
  await page.mouse.move(canvas.x + 4, canvas.y + 4) // off the model, still on the canvas
  await page.waitForTimeout(400)

  expect(await linkedZone(page), 'the highlight stuck after the cursor left').toBe('')
})
