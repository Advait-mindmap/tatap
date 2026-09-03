/**
 * Task 14: the 4D time scrubber.
 *
 * VISUALIZATION_SPEC.md section 2: a scrubber runs from project start to RFS, and as it advances
 * each zone appears and changes state in step with its stage.
 *
 * The assertions read the rendered framebuffer, not the DOM. A slider that moves while the model
 * stays identical is exactly the failure worth catching, and "the input's value changed" cannot
 * tell the two apart.
 *
 * Run against the stub API so the run is fast, free and deterministic:
 *     LLM_PROVIDER=stub python -m uvicorn backend.app.main:app --port 8000
 */

import { expect, test, type Page } from '@playwright/test'

import { apiReachable, completedRun } from './support'
import { buildTimeline, zoneStateAt } from '../src/timeline'

const SHOTS = 'e2e/screenshots'

test.beforeAll(async () => {
  test.skip(!(await apiReachable()), 'no API on :8000 — start it with LLM_PROVIDER=stub')
})

/** Finish a run and switch to the 3D view, where the scrubber lives. */
async function open4D(page: Page): Promise<void> {
  await completedRun(page)
  await page.getByTestId('view-3d-button').click()
  await expect(page.getByTestId('view-3d')).toBeVisible()
  await expect(page.getByTestId('time-scrubber')).toBeVisible()
  await page.waitForTimeout(1200) // let the first frame paint
}

interface Frame {
  /** Fraction of the canvas that is coloured geometry. */
  lit: number
  meanLuminance: number
  /** Mean brightness per cell of an 8x8 grid, for measuring how far two frames differ. */
  cells: number[]
}

/**
 * How different two frames are: mean absolute difference per cell, in luminance units.
 *
 * A quantised string compared for equality was the first attempt and it was too blunt - day 0
 * (one zone) and day 184 (thirty-three) hashed to the same bucket. Measuring the distance says
 * how different, which is what the claim actually is.
 */
function distance(a: Frame, b: Frame): number {
  const total = a.cells.reduce((sum, value, i) => sum + Math.abs(value - b.cells[i]), 0)
  return total / a.cells.length
}

/**
 * Sample the WebGL framebuffer.
 *
 * Copying into a 2D canvas right after a rendered frame is the reliable way to read it back;
 * `preserveDrawingBuffer` is not set, so a plain toDataURL can come back blank.
 */
async function frame(page: Page): Promise<Frame> {
  return page.evaluate(async () => {
    const gl = document.querySelector('canvas') as HTMLCanvasElement
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))
    const copy = document.createElement('canvas')
    copy.width = gl.width
    copy.height = gl.height
    const ctx = copy.getContext('2d')!
    ctx.drawImage(gl, 0, 0)
    const { data } = ctx.getImageData(0, 0, copy.width, copy.height)

    let lit = 0
    let sampled = 0
    let luminance = 0
    // A coarse grid of cell brightnesses: two models that differ in what is built produce
    // different cells, while noise between identical frames does not.
    const cells = new Array(8 * 8).fill(0)
    const counts = new Array(8 * 8).fill(0)

    const step = 4
    for (let y = 0; y < copy.height; y += step) {
      for (let x = 0; x < copy.width; x += step) {
        const i = (y * copy.width + x) * 4
        const r = data[i]
        const g = data[i + 1]
        const b = data[i + 2]
        sampled += 1
        const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
        luminance += lum
        if (Math.max(r, g, b) - Math.min(r, g, b) > 30 && Math.max(r, g, b) > 60) lit += 1

        const cell =
          Math.min(7, Math.floor((y / copy.height) * 8)) * 8 +
          Math.min(7, Math.floor((x / copy.width) * 8))
        cells[cell] += lum
        counts[cell] += 1
      }
    }

    return {
      lit: lit / sampled,
      meanLuminance: luminance / sampled,
      cells: cells.map((total, i) => total / Math.max(counts[i], 1)),
    }
  })
}

/** Drag the range input to a fraction of its track, as a user would. */
async function dragTo(page: Page, fraction: number): Promise<void> {
  const slider = page.getByTestId('scrubber-range')
  const box = (await slider.boundingBox())!
  const y = box.y + box.height / 2
  await page.mouse.move(box.x + 4, y)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width * fraction, y, { steps: 20 })
  await page.mouse.up()
  await page.waitForTimeout(900) // the model re-renders and the fit settles
}

const dayOf = async (page: Page): Promise<number> =>
  Number(((await page.getByTestId('scrubber-day').textContent()) ?? '').match(/\d+/)?.[0] ?? -1)

test('the scrubber runs from project start to RFS', async ({ page }) => {
  await open4D(page)

  const label = await page.getByTestId('scrubber-day').textContent()
  expect(label, 'the scrubber has no timeline to run along').toMatch(/Day \d+ of \d+/)

  // A finished run's RFS is final, so nothing is marked provisional here.
  await expect(page.getByTestId('scrubber-provisional')).toHaveCount(0)

  const rfs = Number(label!.match(/of (\d+)/)![1])
  expect(rfs, 'RFS is day zero, so there is no programme to scrub').toBeGreaterThan(10)

  // It opens on the finished plan, and Start rewinds to day 0.
  expect(await dayOf(page)).toBe(rfs)
  await page.getByTestId('scrubber-start').click()
  await page.waitForTimeout(600)
  expect(await dayOf(page)).toBe(0)
})

test('dragging the scrubber shows the model at three different points in the build', async ({
  page,
}) => {
  await open4D(page)

  // --- day 0: the start of the build
  await dragTo(page, 0)
  const startDay = await dayOf(page)
  const atStart = await frame(page)
  const startCount = await page.getByTestId('built-count').textContent()
  await page.screenshot({ path: `${SHOTS}/14-01-day-zero.png` })

  // --- midway: a point where the build genuinely differs from its start.
  //
  // Not a fixed fraction. Nothing completes until day 85 on this plan, so dragging to the
  // halfway mark lands in a flat stretch and compares day 0 with itself - which is how this
  // test first "failed": correctly, on a badly chosen sample. Searching for a distinct state
  // asserts the real claim (there IS a distinguishable mid-build state) instead of assuming
  // where it sits.
  let midDay = -1
  let midCount: string | null = null
  for (const fraction of [0.3, 0.45, 0.6, 0.75, 0.9]) {
    await dragTo(page, fraction)
    const count = await page.getByTestId('built-count').textContent()
    if (count !== startCount) {
      midDay = await dayOf(page)
      midCount = count
      break
    }
  }
  expect(midDay, 'no point in the build differs from its start').toBeGreaterThan(0)
  const atMid = await frame(page)
  await page.screenshot({ path: `${SHOTS}/14-02-midway.png` })

  // --- RFS: the finished facility
  await dragTo(page, 1)
  const rfsDay = await dayOf(page)
  const atRfs = await frame(page)
  const rfsCount = await page.getByTestId('built-count').textContent()
  await page.screenshot({ path: `${SHOTS}/14-03-rfs.png` })

  console.log('day 0 :', startDay, startCount)
  console.log('midway:', midDay, midCount)
  console.log('RFS   :', rfsDay, rfsCount)
  console.log('frame distance  start->mid', distance(atStart, atMid).toFixed(2),
    ' mid->rfs', distance(atMid, atRfs).toFixed(2),
    ' start->rfs', distance(atStart, atRfs).toFixed(2))

  // The scrubber actually moved through the programme.
  expect(startDay).toBe(0)
  expect(midDay).toBeGreaterThan(startDay)
  expect(rfsDay).toBeGreaterThan(midDay)

  // And the MODEL changed with it — this is the assertion that matters. A scrubber whose label
  // updates while the geometry stays put is not a 4D view. The threshold is in luminance units
  // per cell: comfortably above frame-to-frame noise, far below a real change.
  const MOVED = 0.4
  expect(distance(atStart, atMid), 'day 0 and midway render the same model').toBeGreaterThan(MOVED)
  expect(distance(atMid, atRfs), 'midway and RFS render the same model').toBeGreaterThan(MOVED)
  expect(distance(atStart, atRfs), 'day 0 and RFS render the same model').toBeGreaterThan(MOVED)

  // The build only ever grows: work completed by day N is still complete at day N+1.
  const completed = (text: string | null) => Number((text ?? '').match(/(\d+) complete/)?.[1] ?? 0)
  expect(completed(midCount)).toBeGreaterThanOrEqual(completed(startCount))
  expect(completed(rfsCount)).toBeGreaterThan(completed(startCount))

  // At RFS everything the plan builds is complete, and nothing is still in progress.
  expect(rfsCount).toMatch(/0 in progress/)
})

test('zones change state rather than merely appearing', async ({ page }) => {
  await open4D(page)

  await page.getByTestId('scrubber-start').click()
  await page.waitForTimeout(700)
  const start = (await page.getByTestId('built-count').textContent()) ?? ''

  await page.getByTestId('scrubber-rfs').click()
  await page.waitForTimeout(700)
  const end = (await page.getByTestId('built-count').textContent()) ?? ''

  // The distinction the spec asks for: a zone is not simply present or absent, it is in a
  // state. At the start the site is under construction; at RFS it is built.
  expect(start, 'nothing was in progress at the start of the build').toMatch(/[1-9]\d* in progress/)
  expect(end, 'nothing was complete at RFS').toMatch(/[1-9]\d* complete/)
  expect(start).not.toBe(end)
})

// ---------------------------------------------------------------- the model, without a browser

test('a zone is absent, then under construction, then built', () => {
  // Pure check of the state machine the 4D view renders. Worth having separately because the
  // current plan does not exercise the first transition: every stage in it starts on day 0
  // (see the note in the suite header), so nothing is ever "not started". The logic that makes
  // a zone appear is implemented and tested here even though this data cannot show it.
  const span = { fromDay: 10, toDay: 40, source: 'stage' as const }

  expect(zoneStateAt(span, 0)).toBe('not_started')
  expect(zoneStateAt(span, 9)).toBe('not_started')
  expect(zoneStateAt(span, 10)).toBe('in_progress')
  expect(zoneStateAt(span, 39)).toBe('in_progress')
  expect(zoneStateAt(span, 40)).toBe('complete')
  expect(zoneStateAt(span, 999)).toBe('complete')
})

test('a zone with no timeline at all is not silently dropped from the model', () => {
  // An unknown zone is still part of the facility. Hiding it would quietly shrink the plan.
  const timeline = buildTimeline({
    zones: [{ id: 'zone.site.01', kind: 'site', stage: 'enabling' }],
    rfs_day: 100,
    zone_timeline: {},
    stage_timeline: {},
  } as never)

  expect(timeline.spans['zone.site.01'].source).toBe('unknown')
  expect(zoneStateAt(timeline.spans['zone.site.01'], 0)).toBe('in_progress')
})

test('activity-derived timing is preferred over the stage approximation', () => {
  const timeline = buildTimeline({
    zones: [{ id: 'z', kind: 'data_hall', stage: 'superstructure' }],
    rfs_day: 100,
    zone_timeline: { z: { first_day: 5, last_day: 25 } },
    stage_timeline: { superstructure: { from_day: 0, to_day: 90 } },
  } as never)

  expect(timeline.spans.z).toMatchObject({ fromDay: 5, toDay: 25, source: 'activities' })
})
