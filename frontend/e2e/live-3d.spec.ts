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
  // Stepping through a live stream one event at a time is inherently slower than watching it
  // play, and the default budget is a whole-run budget. This test drives the run AND inspects
  // it at three points, so it needs its own.
  test.setTimeout(900_000)

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
    // Six events per round, and one DOM read between rounds rather than three. The first
    // version stepped four at a time and re-read the zone line, the state line and the run
    // status every round - roughly sixteen hundred clicks and five thousand reads for a
    // hundred-event stream, which ran the test out of time while the states themselves were
    // being captured correctly.
    let last: Built | null = null
    for (let round = 0; round < 300; round += 1) {
      last = await built(page)
      if (ready(last)) return last
      await step(6)
    }
    throw new Error(`never reached "${label}" (last: ${JSON.stringify(last)})`)
  }

  // 1. Ground broken: the first zone exists because the stage that builds it has started.
  const early = await advanceUntil('first zone under construction', (b) => b.present > 0)

  // Mid-run the timeline is computed from what has been assembled SO FAR, so RFS grows as the
  // walk proceeds. It has to say so: unmarked, "Day 170 of 170" partway through a run that
  // finishes at 880 reads as a completion date.
  //
  // Asserted HERE rather than before the first step. The scrubber only renders once the plan
  // has a non-zero RFS, and sequencing the construction stages means the opening events carry
  // no dated work yet - so the marker was being demanded of a scrubber that did not exist. By
  // this point a zone is under construction, so there is certainly a timeline, and the run is
  // certainly still streaming, which is exactly the state the marker is about.
  await expect(page.getByTestId('scrubber-provisional')).toBeVisible()
  await expect(page.getByTestId('scrubber-day')).toContainText('so far')
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
    await page.waitForTimeout(250)
  }
  await page.waitForTimeout(600)
  final = await built(page)
  await page.screenshot({ path: `${SHOTS}/16-03-live-complete.png` })
  console.log('end   :', JSON.stringify(final))

  // And once the run is over the number is final, so the marker goes.
  await expect(page.getByTestId('scrubber-provisional')).toHaveCount(0)
  await expect(page.getByTestId('scrubber-day')).not.toContainText('so far')

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
