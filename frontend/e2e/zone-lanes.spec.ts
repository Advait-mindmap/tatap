/**
 * Tier 2 in the 2D view: a campus of halls has to stay readable.
 *
 * The engine now instances a fragnet per data hall and per electrical room, so a plan that was
 * 118 activities is 257 and a bigger brief goes past 600. Under the old layout — one column per
 * stage, one row per node — `mep_power` alone became a 55-row ribbon roughly 5,700px tall next
 * to a design column of nine. The multiplication would have been a regression in the view even
 * while being an improvement in the plan.
 *
 * So the assertions here are about READABILITY, measured rather than judged: halls occupy their
 * own lanes, the canvas does not grow a ribbon, and hiding a hall hides that hall without taking
 * the programme's spine with it.
 */

import { expect, test } from '@playwright/test'
import { apiReachable, completedRun } from './support'

test.beforeAll(async () => {
  test.skip(!(await apiReachable()), 'planner API not reachable - start uvicorn to run these')
})

interface Card {
  id: string
  stage: string
  zone: string | null
  x: number
  y: number
}

async function cards(page: import('@playwright/test').Page): Promise<Card[]> {
  return page.$$eval('[data-testid="node-card"]', (els) =>
    els.map((el) => {
      const node = el.closest('.react-flow__node') as HTMLElement | null
      const transform = node?.style.transform ?? ''
      const match = /translate\(([-\d.]+)px,\s*([-\d.]+)px\)/.exec(transform)
      return {
        id: el.getAttribute('data-node-id') ?? '',
        stage: el.getAttribute('data-stage') ?? '',
        zone: el.getAttribute('data-zone'),
        x: match ? Number(match[1]) : 0,
        y: match ? Number(match[2]) : 0,
      }
    }),
  )
}

/**
 * ONE run, both assertions. A multi-hall walk takes about twenty minutes through the stub, and
 * two tests each driving their own hit the per-test timeout on the second. Nothing here mutates
 * the plan - one reads positions, the other clicks a filter - so a single run serves both, and
 * the suite gets faster as well as greener.
 */
test('a campus of halls reads as lanes, and a hall can be hidden', async ({ page }) => {
  test.setTimeout(1_800_000)
  await completedRun(page)

  // The canvas OPENS on one zone of each kind, because fifty-six lanes of hall is a plan nobody
  // can read. Lanes are what happens when the zones are shown, so show them: this test is about
  // the layout, and the test below is about the default that hides most of it.
  const showAll = page.getByTestId('show-all-zones')
  await expect(showAll, 'a multi-zone plan did not open with zones held back').toBeVisible()
  await showAll.click()
  await page.waitForTimeout(600)

  const all = await cards(page)
  expect(all.length, 'no cards rendered').toBeGreaterThan(50)

  // Rows per stage lane. This is the number that made the old layout unreadable.
  const rows = new Map<string, number>()
  for (const card of all) {
    const lane = `${card.stage}@${card.x}`
    rows.set(lane, (rows.get(lane) ?? 0) + 1)
  }
  const tallest = Math.max(...rows.values())
  const perStage = new Map<string, number>()
  for (const card of all) {
    perStage.set(card.stage, (perStage.get(card.stage) ?? 0) + 1)
  }
  const busiest = Math.max(...perStage.values())

  console.log(`  cards ${all.length}, busiest stage ${busiest} nodes, tallest lane ${tallest} rows`)

  // The point of lanes: the tallest lane is far shorter than the busiest stage's node count.
  // Without lanes these two numbers are equal by construction.
  expect(tallest, 'the tallest lane is as tall as the whole stage - lanes are not in effect')
    .toBeLessThan(busiest)

  // And a stage with several halls really does occupy several x positions.
  const laneCount = new Map<string, Set<number>>()
  for (const card of all) {
    if (!laneCount.has(card.stage)) laneCount.set(card.stage, new Set())
    laneCount.get(card.stage)!.add(card.x)
  }
  const widest = Math.max(...[...laneCount.values()].map((s) => s.size))
  expect(widest, 'every stage is one lane wide, so no stage was split by zone')
    .toBeGreaterThan(1)

  // ---- and the filter, on the same run --------------------------------------------------
  const filter = page.getByTestId('zone-filter')
  await expect(filter, 'no zone filter rendered for a multi-hall plan').toBeVisible()

  const before = all
  const zones = [...new Set(before.map((c) => c.zone).filter(Boolean))] as string[]
  expect(zones.length, 'this run produced one zone, so this proves nothing').toBeGreaterThan(1)

  const target = zones.sort()[0]
  const spineBefore = before.filter((c) => c.zone !== target).length
  const inTarget = before.filter((c) => c.zone === target).length
  expect(inTarget).toBeGreaterThan(0)

  await filter.getByRole('button').first().click()
  await page.waitForTimeout(400)

  const after = await cards(page)
  console.log(
    `  hid ${target}: ${before.length} -> ${after.length} cards ` +
      `(${inTarget} in that hall, ${spineBefore} elsewhere)`,
  )

  // Everything else survives. Hiding a hall that also removed the gates and the stage headers
  // would be a filter nobody could use twice.
  expect(after.length).toBe(spineBefore)
  expect(after.some((c) => c.zone === target)).toBe(false)
  expect(after.some((c) => c.stage === before[0].stage)).toBe(true)
})


test('a campus opens on one hall of each kind, not on all of them', async ({ page }) => {
  test.setTimeout(1_800_000)
  await completedRun(page)

  const opened = await cards(page)
  const shown = new Set(opened.map((c) => c.zone).filter(Boolean))
  console.log(`  opened with ${opened.length} cards across ${shown.size} zones`)

  // One data hall, one electrical room, and whatever single-instance zones exist - not seven of
  // each. At seven the canvas is sixteen thousand pixels wide and a card is 23px at fit-zoom.
  expect(shown.size, 'the canvas opened on every zone').toBeLessThanOrEqual(6)

  const filter = page.getByTestId('zone-filter')
  await expect(filter).toBeVisible()
  await expect(page.getByTestId('show-all-zones'), 'no way back to the full plan').toBeVisible()

  // Nothing is REMOVED: the filter still lists every zone, so the reader can see what is held
  // back rather than being shown a partial plan that looks complete.
  const listed = await filter.getByRole('button').count()
  expect(listed, 'the filter hides the zones it is hiding').toBeGreaterThan(shown.size)

  await page.getByTestId('show-all-zones').click()
  await page.waitForTimeout(600)
  const full = await cards(page)
  expect(full.length).toBeGreaterThan(opened.length)
  console.log(`  show-all: ${opened.length} -> ${full.length} cards`)
})
