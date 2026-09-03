/**
 * Task 16: the 3D model builds up in step with the live simulation.
 *
 * VISUALIZATION_SPEC.md section 2: "In live mode the 3D model grows as the simulation completes
 * each stage, one by one, matching the 2D flow drawing itself."
 *
 * Deliberately NOT the scrubber. The scrubber (Task 14) replays a finished plan from a computed
 * timeline; this is the model following the event stream while the run is still happening, which
 * is a different code path and a different claim. Nothing here touches the slider.
 *
 * Run against the stub API so the run is fast, free and deterministic:
 *     LLM_PROVIDER=stub python -m uvicorn backend.app.main:app --port 8000
 */

import { expect, test, type Page } from '@playwright/test'

import { apiReachable, extractBrief } from './support'

const SHOTS = 'e2e/screenshots'

test.beforeAll(async () => {
  test.skip(!(await apiReachable()), 'no API on :8000 — start it with LLM_PROVIDER=stub')
})

interface Built {
  /** Zones with any geometry on screen: under construction or finished. */
  present: number
  /** Zones in the site plan, known from the start of the run. */
  total: number
  complete: number
  inProgress: number
  stages: string
}

/** What the 3D panel says is built right now. */
async function built(page: Page): Promise<Built> {
  const zoneText = (await page.getByTestId('zone-count').textContent()) ?? ''
  const stateText = (await page.getByTestId('built-count').textContent()) ?? ''
  const status = (await page.getByTestId('run-status').textContent()) ?? ''
  return {
    present: Number(zoneText.match(/^(\d+)/)?.[1] ?? 0),
    total: Number(zoneText.match(/of (\d+) zones/)?.[1] ?? 0),
    complete: Number(stateText.match(/(\d+) complete/)?.[1] ?? 0),
    inProgress: Number(stateText.match(/(\d+) in progress/)?.[1] ?? 0),
    stages: status.replace(/\s+/g, ' ').trim(),
  }
}

/** Answer whatever fork is on screen, if one is. Returns whether it answered. */
async function answerIfAsked(page: Page): Promise<boolean> {
  if (!(await page.getByTestId('decision-prompt').count())) return false
  const option = page.getByTestId('decision-option').first()
  if (await option.count()) {
    await option.click()
  } else {
    await page.getByTestId('decision-answer-input').fill('Confirmed')
    await page.getByTestId('decision-submit').click()
  }
  return true
}

test('the 3D model builds up as the live run streams', async ({ page }) => {
  await extractBrief(page)

  // Start the run and go straight to 3D, so the model is on screen while the plan is built
  // rather than being inspected after the fact.
  await page.getByTestId('run-button').click()
  await expect(page.getByTestId('run-panel')).toBeVisible()
  await page.getByTestId('view-3d-button').click()

  // The site plan arrives with simulation_started, so there is geometry before any stage runs.
  await expect(page.getByTestId('view-3d')).toBeVisible({ timeout: 60_000 })
  await expect(page.getByTestId('zone-count')).toBeVisible({ timeout: 60_000 })

  /**
   * Hold the LIVE stream and advance it deliberately.
   *
   * Polling a playing run cannot sample this: with the stub the whole stream drains in a second
   * or two, and consecutive 120ms reads went straight from "one zone breaking ground" to "all
   * thirty-four complete". Pausing does not change what is being tested - these are the same
   * live events from the same run, applied one at a time instead of in a burst - it just makes
   * the intermediate states observable. Nothing here touches the scrubber.
   */
  await page.getByTestId('pause-btn').click()
  await expect(page.getByTestId('play-btn')).toBeVisible()

  async function step(times: number): Promise<void> {
    for (let i = 0; i < times; i += 1) {
      if (await answerIfAsked(page)) {
        // A fork halts the run until it is answered, and answering resumes playing.
        if (await page.getByTestId('pause-btn').count()) {
          await page.getByTestId('pause-btn').click()
        }
        continue
      }
      const stepBtn = page.getByTestId('step-btn')
      if (!(await stepBtn.isEnabled())) return
      await stepBtn.click()
    }
  }

  async function advanceUntil(label: string, ready: (b: Built) => boolean): Promise<Built> {
    let last: Built | null = null
    for (let round = 0; round < 400; round += 1) {
      last = await built(page)
      if (ready(last)) return last
      await step(4)
      await page.waitForTimeout(40)
    }
    throw new Error(`never reached "${label}" (last: ${JSON.stringify(last)})`)
  }

  // 1. Ground broken: the first zone exists because the stage that builds it has started.
  const early = await advanceUntil('first zone under construction', (b) => b.present > 0)
  await page.waitForTimeout(400)
  await page.screenshot({ path: `${SHOTS}/16-01-live-early.png` })
  console.log('early :', JSON.stringify(early))

  // 2. Part built: something finished while something else is still going up. This is the state
  // the whole task is about - a model mid-construction, not a before and an after.
  const mid = await advanceUntil(
    'partly built',
    (b) => b.complete > 0 && b.complete < b.total,
  )
  await page.waitForTimeout(400)
  await page.screenshot({ path: `${SHOTS}/16-02-live-midway.png` })
  console.log('midway:', JSON.stringify(mid))

  // 3. Let it run out, and capture the finished facility.
  await page.getByTestId('play-btn').click()
  let final = await built(page)
  for (let tick = 0; tick < 600; tick += 1) {
    await answerIfAsked(page)
    final = await built(page)
    if (/Simulation complete/.test(final.stages) && final.inProgress === 0) break
    await page.waitForTimeout(150)
  }
  await page.waitForTimeout(600)
  final = await built(page)
  await page.screenshot({ path: `${SHOTS}/16-03-live-complete.png` })
  console.log('end   :', JSON.stringify(final))

  const samples = [early, mid, final]

  // The model GREW. Zones appear as their stages start and complete as they finish; nothing is
  // ever un-built. This is the assertion that separates a live 3D view from a static one shown
  // three times.
  expect(mid.present).toBeGreaterThanOrEqual(early.present)
  expect(final.present).toBeGreaterThanOrEqual(mid.present)
  expect(mid.complete).toBeGreaterThan(early.complete)
  expect(final.complete).toBeGreaterThan(mid.complete)

  // And the three states are genuinely distinct, not one reading logged three times.
  const signatures = samples.map((s) => `${s.present}/${s.complete}/${s.inProgress}`)
  expect(new Set(signatures).size, `states were ${signatures.join(' , ')}`).toBe(3)

  // At the end everything the plan builds is built.
  expect(final.stages).toMatch(/Simulation complete/)
  expect(final.inProgress).toBe(0)
  expect(final.complete).toBe(final.present)
})

test('the site plan arrives before any stage has run', async ({ page }) => {
  // Zones are a deterministic function of the brief, so the model has something to build from
  // the moment the run starts. Before this, `zones` came only with the authoritative output at
  // a settle point, and the 3D view sat empty for most of a run and then appeared complete in
  // one jump - the opposite of watching a plan get built.
  await extractBrief(page)
  await page.getByTestId('run-button').click()
  await page.getByTestId('view-3d-button').click()

  await expect(page.getByTestId('zone-count')).toBeVisible({ timeout: 60_000 })
  const first = await built(page)

  // The site PLAN is known immediately - all 34 zones - even though none is built yet. That is
  // the enabling change: before it, `zones` arrived only with the authoritative output at a
  // settle point, so the 3D view had nothing to draw for most of a run and then appeared
  // complete in one jump.
  expect(first.total, 'the site plan had not arrived when the run started').toBeGreaterThan(0)
  expect(first.complete, 'zones were already complete before any stage finished').toBe(0)
})
