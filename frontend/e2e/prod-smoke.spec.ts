/**
 * Deployment smoke test: one real run, everything checked against it.
 *
 * Runs ONLY when E2E_BASE_URL is set, and is deliberately not in CI. Against production it hits
 * the real provider, so each run spends credits and counts against RUNS_PER_CLIENT_DAILY. The
 * ordinary suites start a run per test, which would exhaust that cap and prove nothing except
 * that the cap works — so this drives a single simulation and asks every question of it.
 *
 *     E2E_BASE_URL=https://tatap-app-production.up.railway.app npx playwright test prod-smoke
 */

import { expect, test } from '@playwright/test'

import { CHENNAI_BRIEF } from './support'

const SHOTS = 'e2e/screenshots'

test.skip(!process.env.E2E_BASE_URL, 'deployment smoke test; set E2E_BASE_URL to run it')

test('production serves the sequenced plan, the 4D scrubber and linked hover', async ({
  page,
}) => {
  test.setTimeout(900_000) // a real run reasons thirteen stages through the live provider

  // ---------------------------------------------------------------- one real run
  await page.goto('/')
  await expect(page.getByTestId('intake-screen')).toBeVisible()
  await page.getByTestId('brief-input').fill(CHENNAI_BRIEF)
  await page.getByTestId('extract-button').click()
  await expect(page.getByTestId('confirm-screen')).toBeVisible({ timeout: 180_000 })
  await page.getByTestId('run-button').click()

  for (let round = 0; round < 60; round += 1) {
    const status = page.getByTestId('run-status')
    await expect(status).not.toHaveText(/Simulating/, { timeout: 600_000 })
    const text = (await status.textContent()) ?? ''
    if (text.includes('Simulation complete')) break
    if (text.includes('failed')) {
      throw new Error(`run failed: ${await page.getByTestId('run-error').textContent()}`)
    }
    const option = page.getByTestId('decision-option').first()
    if (await option.count()) await option.click()
    else {
      await page.getByTestId('decision-answer-input').fill('Confirmed')
      await page.getByTestId('decision-submit').click()
    }
  }
  await expect(page.getByTestId('run-status')).toHaveText(/Simulation complete/)

  // ------------------------------------------------- the sequenced schedule reached prod
  await page.getByTestId('view-3d-button').click()
  await expect(page.getByTestId('time-scrubber')).toBeVisible({ timeout: 60_000 })

  const label = (await page.getByTestId('scrubber-day').textContent()) ?? ''
  const rfs = Number(label.match(/of (\d+)/)?.[1] ?? 0)
  console.log('PROD scrubber:', label.trim())

  // 162 was the unsequenced plan: every stage starting on day 0 because the design and
  // procurement fragnets did not exist and the delivery gates carried no lead time. A
  // sequenced programme is several hundred days, and this is how we know prod has the new
  // libraries and the forward pass rather than a stale image.
  expect(rfs, `RFS is ${rfs} days — production is still on the unsequenced plan`).toBeGreaterThan(
    400,
  )
  // The run is finished, so the number is final and must not be marked provisional.
  await expect(page.getByTestId('scrubber-provisional')).toHaveCount(0)
  await page.screenshot({ path: `${SHOTS}/prod-01-sequenced.png` })

  // ------------------------------------------------------------------ the 4D scrubber
  const slider = page.getByTestId('scrubber-range')
  const box = (await slider.boundingBox())!
  const readBuilt = async () =>
    (await page.getByTestId('built-count').textContent()) ?? ''

  const atRfs = await readBuilt()
  await page.getByTestId('scrubber-start').click()
  await page.waitForTimeout(900)
  const atStart = await readBuilt()
  console.log('PROD 4D:  start', atStart.trim(), '| RFS', atRfs.trim())
  expect(atStart, 'the scrubber did not change the model').not.toBe(atRfs)
  await page.screenshot({ path: `${SHOTS}/prod-02-scrubber-day-zero.png` })

  // Drag it, rather than only using the jump buttons.
  await page.mouse.move(box.x + 4, box.y + box.height / 2)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width * 0.55, box.y + box.height / 2, { steps: 20 })
  await page.mouse.up()
  await page.waitForTimeout(900)
  const atMid = await readBuilt()
  console.log('PROD 4D:  mid  ', atMid.trim())
  expect(new Set([atStart, atMid, atRfs]).size, 'dragging produced no distinct states')
    .toBeGreaterThan(1)

  // ------------------------------------------------------------------- linked hover
  await page.getByTestId('view-split-button').click()
  await expect(page.getByTestId('view-split')).toBeVisible()
  await page.waitForTimeout(1500)

  const zoned = page.locator(
    '.view-split-2d [data-testid="node-card"][data-zone]:not([data-zone=""])',
  )
  await expect.poll(async () => zoned.count(), { timeout: 20_000 }).toBeGreaterThan(0)

  let linked = ''
  const count = await zoned.count()
  for (let i = 0; i < count && !linked; i += 1) {
    const card = await zoned.nth(i).boundingBox()
    if (!card) continue
    await page.mouse.move(8, 8)
    await page.waitForTimeout(50)
    await page.mouse.move(card.x + card.width / 2, card.y + card.height / 2, { steps: 12 })
    await page.mouse.move(card.x + card.width / 2 + 3, card.y + card.height / 2 + 3)
    await page.waitForTimeout(250)
    linked =
      (await page.locator('.app[data-linked-zone]').first().getAttribute('data-linked-zone')) ?? ''
  }

  expect(linked, 'hovering a 2D node lit no zone in the 3D model').not.toBe('')
  await expect(page.getByTestId('linked-zone-label')).toBeVisible()
  console.log('PROD linked hover:', linked, '->', await page.getByTestId('linked-zone-label').textContent())
  await page.screenshot({ path: `${SHOTS}/prod-03-linked-hover.png` })
})
