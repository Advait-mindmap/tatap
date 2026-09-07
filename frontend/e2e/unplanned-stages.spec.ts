/**
 * A run that is not actually complete must say so where the reader is already looking.
 *
 * A real run finished as "Simulation complete" with 371 activities and 13/13 stages, while
 * mep_power, commissioning and procurement contained no work at all. That is a legitimate
 * outcome - the planner answered "Stop and obtain real data" at each low-confidence fork, then
 * "In scope, record the plan as incomplete" at the coverage fork that followed - and both answers
 * were honoured exactly. What was missing is that nothing SAID so: the only evidence was
 * `blocks_export` flags and an `[UNANCHORED]` gate name a reader had to go and find, and the
 * symptom reported back was "the commissioning gate has no predecessors" - someone diagnosing
 * gate wiring when three stages had simply never been planned.
 *
 * HOW THIS IS TESTED, and why not the way the bug arrived. The stub reasoner selects every
 * package mechanically whatever the fork is answered, so "Stop and obtain real data" leaves the
 * plan identical - 243 mep_power tasks either way. Only the live model actually stops selecting.
 * Driving the reported answers through the stub would produce a fully planned run and a test that
 * could never see the banner, which is what the first version of this file did.
 *
 * So the browser tests use two briefs that differ in coverage for a real reason: Chennai has no
 * city pathway file, so its approvals stage completes with no work, and Navi Mumbai has one, so
 * every stage is covered. The condition under test - a completed stage with no tasks - is the
 * same one the reported run hit; only the cause differs. The reported SHAPE, where a stage holds
 * gates or delivery milestones but no work, is pinned directly below against the real function,
 * because no stub run produces it.
 *
 * BOTH DIRECTIONS MATTER. A banner that always appears is wallpaper: it would show on every
 * healthy run and be ignored by the time it mattered.
 */

import { expect, test } from '@playwright/test'
import { apiReachable, extractBrief, NAVI_MUMBAI_BRIEF } from './support'

test.beforeAll(async () => {
  test.skip(!(await apiReachable()), 'planner API not reachable - start uvicorn to run these')
})

async function runToEnd(page: import('@playwright/test').Page, brief?: string) {
  await extractBrief(page, brief)
  await page.getByTestId('run-button').click()
  await expect(page.getByTestId('run-panel')).toBeVisible()

  for (let round = 0; round < 60; round += 1) {
    const status = page.getByTestId('run-status')
    await expect(status).not.toHaveText(/Simulating/, { timeout: 300_000 })
    const text = (await status.textContent()) ?? ''
    if (/complete/i.test(text)) return
    if (/failed/i.test(text)) {
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
  }
  throw new Error('the run did not finish within 60 decision rounds')
}

test('a run with a stage left unplanned says so at the top of the panel', async ({ page }) => {
  test.setTimeout(900_000)

  // Chennai has no statutory pathway in the library, so approvals completes with no work.
  await runToEnd(page)
  await expect(page.getByTestId('run-status')).toHaveText(/complete/i)

  const banner = page.getByTestId('unplanned-banner')
  await expect(
    banner,
    'the run reports complete with a stage unplanned and says nothing about it',
  ).toBeVisible()

  const text = ((await banner.textContent()) ?? '').replace(/\s+/g, ' ').trim()
  const count = Number(await banner.getAttribute('data-unplanned-count'))
  console.log(`  banner: ${text.slice(0, 140)}`)

  expect(count).toBeGreaterThan(0)
  expect(text).toMatch(/not planned/i)
  // Names the stage, so the reader knows WHAT is missing without hunting for a gate.
  expect(text.toLowerCase()).toContain('approvals')

  // Next to the status it contradicts, not buried under the decision history.
  const statusBox = await page.getByTestId('run-status').boundingBox()
  const bannerBox = await banner.boundingBox()
  expect(bannerBox!.y - statusBox!.y, 'the banner is not near the status it qualifies')
    .toBeLessThan(200)
})

test('a fully covered run shows no banner at all', async ({ page }) => {
  test.setTimeout(900_000)

  // Navi Mumbai HAS a pathway file, so approvals instances real statutory work and every stage
  // is covered.
  await runToEnd(page, NAVI_MUMBAI_BRIEF)
  await expect(page.getByTestId('run-status')).toHaveText(/complete/i)

  await expect(
    page.getByTestId('unplanned-banner'),
    'a fully planned run is being reported as incomplete',
  ).toHaveCount(0)

  // And it really is fully planned - otherwise this passes for the wrong reason.
  const nodes = await page.getByTestId('node-card').count()
  console.log(`  fully covered run drew ${nodes} nodes with no banner`)
  expect(nodes).toBeGreaterThan(50)
})

// ------------------------------------------------------------ the reported shape, directly
//
// No stub run produces a stage that holds gates or delivery milestones but no work, because the
// stub always selects every package. That shape is exactly what the reported run had, so it is
// pinned here against the real function rather than left untested because it is inconvenient to
// stage in a browser.

test('the reported shape: stages holding only gates or milestones count as unplanned', async ({
  page,
}) => {
  await page.goto('/')
  const result = await page.evaluate(async () => {
    const { readCoverage } = await import('/src/coverage.ts')
    // Modelled on run-57331b85: mep_power and commissioning hold only their gate, procurement
    // only delivery milestones, approvals only statutory work - which IS real work.
    const output = {
      project_meta: {
        stages_completed: [
          'design', 'approvals', 'procurement', 'mep_power', 'commissioning', 'handover',
        ],
      },
      activities: [
        { stage: 'design', type: 'task' },
        { stage: 'approvals', type: 'task' },
        { stage: 'procurement', type: 'milestone' },
        { stage: 'procurement', type: 'milestone' },
        { stage: 'mep_power', type: 'gate', blocks_export: true },
        { stage: 'commissioning', type: 'gate', blocks_export: true },
        { stage: 'handover', type: 'task' },
      ],
    }
    return readCoverage(output as never)
  })

  console.log(`  unplanned: ${result.unplanned.join(', ')}  blocked: ${result.exportBlocked}`)
  // Procurement is NOT covered by two delivery milestones, and mep_power is not covered by its
  // own gate - that reading is what let the reported run look complete.
  expect(result.unplanned).toEqual(['procurement', 'mep_power', 'commissioning'])
  // Approvals carries statutory work, which is real work with real durations.
  expect(result.planned).toContain('approvals')
  expect(result.exportBlocked).toBe(true)
})

test('a stage the run never reached is not reported as unplanned', async ({ page }) => {
  await page.goto('/')
  const result = await page.evaluate(async () => {
    const { readCoverage } = await import('/src/coverage.ts')
    // A halted run: only design finished. Everything after it is unstarted, not unplanned, and
    // listing twelve "missing" stages on a run that is simply still going would be noise.
    return readCoverage({
      project_meta: { stages_completed: ['design'] },
      activities: [{ stage: 'design', type: 'task' }, { stage: 'approvals', type: 'task' }],
    } as never)
  })
  console.log(`  halted run unplanned: ${JSON.stringify(result.unplanned)}`)
  expect(result.unplanned).toEqual([])
})
