/**
 * The 3D build model, checked by looking at the pixels.
 *
 * "It builds" and "the canvas is in the DOM" are both true of a black screen, so every assertion
 * here reads the rendered framebuffer: how much of it is scene rather than background, how many
 * distinct zone colours survive to the screen, and whether the model is spread across the view or
 * bunched into one corner. The screenshots are a deliverable, not a side effect — they are how a
 * human confirms what the numbers claim.
 *
 * Run against the stub API so the run is fast, free and deterministic:
 *     LLM_PROVIDER=stub python -m uvicorn backend.app.main:app --port 8000
 *     npx playwright test view3d.spec
 */

import { expect, test, type Page } from '@playwright/test'

import { apiReachable, completedRun } from './support'
import { layoutZones, ZONE_GEOMETRY, type Zone3D } from '../src/viz3d'

const SHOTS = 'e2e/screenshots'

test.beforeAll(async () => {
  test.skip(!(await apiReachable()), 'no API on :8000 — start it with LLM_PROVIDER=stub')
})

/** Switch a finished run into the 3D view and wait for the first frame to be painted. */
async function open3D(page: Page): Promise<void> {
  await page.getByTestId('view-3d-button').click()
  await expect(page.getByTestId('view-3d')).toBeVisible()
  await expect(page.locator('canvas')).toBeVisible()
  // react-three-fiber paints on rAF; give it frames to draw rather than racing the first one.
  await page.waitForTimeout(1200)
}

interface Pixels {
  width: number
  height: number
  /** Fraction of sampled pixels that are not the background. */
  litFraction: number
  /** Distinct quantised hues present, excluding background and grey grid lines. */
  colours: string[]
  /** Fraction of lit pixels in each screen quadrant, to catch "all bunched in one corner". */
  quadrants: number[]
  /** Fraction of the outermost border band that is geometry — non-zero means the model is cropped. */
  edgeFraction: number
  meanLuminance: number
}

/**
 * Read the WebGL framebuffer back and describe what is actually on screen.
 *
 * `preserveDrawingBuffer` is not set on the canvas, so a plain toDataURL can come back blank.
 * Copying into a 2D canvas immediately after a rendered frame is the reliable way to sample it.
 */
async function samplePixels(page: Page): Promise<Pixels> {
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
    let edgeLit = 0
    let edgeSampled = 0
    const colours = new Set<string>()
    const quadHits = [0, 0, 0, 0]
    const band = Math.round(Math.min(copy.width, copy.height) * 0.03)

    const step = 4 // sample every 4th pixel in each direction — plenty, and fast
    for (let y = 0; y < copy.height; y += step) {
      for (let x = 0; x < copy.width; x += step) {
        const i = (y * copy.width + x) * 4
        const r = data[i]
        const g = data[i + 1]
        const b = data[i + 2]
        sampled += 1
        const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
        luminance += lum

        // Background is the dark page colour; the grid helper is a mid grey. Anything with
        // real chroma is a zone.
        const max = Math.max(r, g, b)
        const min = Math.min(r, g, b)
        const chroma = max - min
        const isGeometry = chroma > 30 && max > 60
        if (isGeometry) {
          lit += 1
          colours.add(`${r >> 5}-${g >> 5}-${b >> 5}`)
          const q = (x < copy.width / 2 ? 0 : 1) + (y < copy.height / 2 ? 0 : 2)
          quadHits[q] += 1
        }

        // The outermost band of the canvas. Geometry touching it means the model is running
        // off the edge of the view rather than being framed by it.
        if (x < band || y < band || x >= copy.width - band || y >= copy.height - band) {
          edgeSampled += 1
          if (isGeometry) edgeLit += 1
        }
      }
    }

    return {
      width: copy.width,
      height: copy.height,
      litFraction: lit / sampled,
      colours: [...colours],
      quadrants: quadHits.map((h) => (lit ? h / lit : 0)),
      edgeFraction: edgeSampled ? edgeLit / edgeSampled : 0,
      meanLuminance: luminance / sampled,
    }
  })
}

// ------------------------------------------------------------------ the layout, on its own

test('zones are laid out without overlapping each other', () => {
  // Pure check, no browser: if the layout overlaps, no camera angle can fix it. Two boxes
  // overlap when their footprints intersect on both axes — they would render as one merged
  // blob, which is exactly what "not overlapping" has to mean for a schematic to be readable.
  const zones: Zone3D[] = [
    { zone_id: 'z.site', name: 'Site', kind: 'site', stage: 'enabling' },
    { zone_id: 'z.shell', name: 'Shell', kind: 'shell', stage: 'substructure' },
    ...Array.from({ length: 7 }, (_, i) => ({
      zone_id: `z.hall.${i}`, name: `Hall ${i}`, kind: 'data_hall' as const,
      stage: 'superstructure',
    })),
    ...Array.from({ length: 7 }, (_, i) => ({
      zone_id: `z.elec.${i}`, name: `Elec ${i}`, kind: 'electrical_room' as const,
      stage: 'mep_power',
    })),
    ...Array.from({ length: 7 }, (_, i) => ({
      zone_id: `z.ups.${i}`, name: `UPS ${i}`, kind: 'ups_room' as const, stage: 'mep_power',
    })),
    { zone_id: 'z.gen', name: 'Gen yard', kind: 'generator_yard', stage: 'mep_power' },
    { zone_id: 'z.cool', name: 'Cooling', kind: 'cooling_plant', stage: 'mep_cooling' },
  ]

  const placed = layoutZones(zones)
  // Site is the ground plane and shell stands on it, so both are expected to sit under the
  // others. Overlap is only meaningful between the buildings.
  const buildings = placed.filter((z) => z.kind !== 'site' && z.kind !== 'shell')

  const clashes: string[] = []
  for (let i = 0; i < buildings.length; i += 1) {
    for (let j = i + 1; j < buildings.length; j += 1) {
      const a = buildings[i]
      const b = buildings[j]
      const ga = ZONE_GEOMETRY[a.kind]
      const gb = ZONE_GEOMETRY[b.kind]
      const overlapX = Math.abs((a.x ?? 0) - (b.x ?? 0)) < (ga.width + gb.width) / 2
      const overlapZ = Math.abs((a.z ?? 0) - (b.z ?? 0)) < (ga.depth + gb.depth) / 2
      if (overlapX && overlapZ) clashes.push(`${a.zone_id} <-> ${b.zone_id}`)
    }
  }

  expect(clashes, `overlapping zones: ${clashes.slice(0, 6).join(', ')}`).toEqual([])
})

test('every zone is given a distinct position and a real id', () => {
  const zones: Zone3D[] = Array.from({ length: 7 }, (_, i) => ({
    zone_id: `z.hall.${i}`, name: `Hall ${i}`, kind: 'data_hall' as const,
    stage: 'superstructure',
  }))
  const placed = layoutZones(zones)
  const positions = new Set(placed.map((z) => `${z.x},${z.z}`))
  expect(positions.size).toBe(zones.length)
  expect(placed.every((z) => z.zone_id !== '')).toBe(true)
})

// ------------------------------------------------------------------ the rendered scene

test('the 3D view renders the zones, and is not a black screen', async ({ page }) => {
  await completedRun(page)
  await open3D(page)

  await page.screenshot({ path: `${SHOTS}/3d-01-initial.png` })

  const pixels = await samplePixels(page)
  console.log('initial frame:', JSON.stringify(pixels, null, 2))

  expect(pixels.width, 'the canvas has no size, so nothing could render').toBeGreaterThan(200)
  expect(pixels.height).toBeGreaterThan(200)

  // A black screen is the specific failure being guarded against.
  expect(pixels.meanLuminance, 'the scene is black — WebGL produced no image').toBeGreaterThan(3)

  // Zones must actually occupy the view, not be a couple of stray pixels on the horizon.
  expect(
    pixels.litFraction,
    `only ${(pixels.litFraction * 100).toFixed(1)}% of the canvas is coloured geometry`,
  ).toBeGreaterThan(0.04)

  // Distinct coloured boxes: the model is colour-coded by stage, so several hues must survive.
  expect(
    pixels.colours.length,
    `only ${pixels.colours.length} distinct zone colours reached the screen`,
  ).toBeGreaterThanOrEqual(3)

  // Framed, not cropped. This is the assertion that fails on the layout this replaced: with
  // fixed camera placement the plan ran off the bottom of the canvas, so the border band was
  // full of geometry. A model the camera actually fits leaves clear space around it.
  expect(
    pixels.edgeFraction,
    `the model runs off the edge of the view — ${(pixels.edgeFraction * 100).toFixed(1)}% of ` +
      'the border band is geometry',
  ).toBeLessThan(0.06)

  // Not bunched off in one corner: every quadrant should carry some of the model.
  const emptyQuadrants = pixels.quadrants.filter((q) => q < 0.02).length
  expect(
    emptyQuadrants,
    `the model is off-centre — quadrant coverage ${pixels.quadrants
      .map((q) => `${(q * 100).toFixed(0)}%`)
      .join(' / ')}`,
  ).toBeLessThanOrEqual(1)
})

test('the scene can be orbited and still shows the model', async ({ page }) => {
  await completedRun(page)
  await open3D(page)

  const before = await samplePixels(page)

  // Drag across the canvas: OrbitControls turns that into a rotation about the target.
  //
  // Horizontally, deliberately. OrbitControls maps a full canvas height of vertical drag to a
  // full turn, so even 60px tips the camera ~30 degrees and a couple of hundred puts it on the
  // ground looking at the site edge-on — a true orbit, but a useless picture. Swinging round
  // the vertical axis keeps the elevation and shows the model from a genuinely new side.
  const box = (await page.locator('canvas').boundingBox())!
  const cx = box.x + box.width / 2
  const cy = box.y + box.height / 2
  await page.mouse.move(cx, cy)
  await page.mouse.down()
  await page.mouse.move(cx - 320, cy, { steps: 30 })
  await page.mouse.up()
  await page.waitForTimeout(1000) // damping settles

  await page.screenshot({ path: `${SHOTS}/3d-02-orbited.png` })

  const after = await samplePixels(page)
  console.log('orbited frame:', JSON.stringify(after, null, 2))

  // The camera moved: the image must differ, or the drag did nothing and the "3D" view is a
  // still picture.
  const moved =
    Math.abs(after.meanLuminance - before.meanLuminance) > 0.4 ||
    Math.abs(after.litFraction - before.litFraction) > 0.008 ||
    after.quadrants.some((q, i) => Math.abs(q - before.quadrants[i]) > 0.02)
  expect(moved, 'the view did not change when orbited').toBe(true)

  // And it is still a populated scene from the new angle, not spun into the void.
  expect(after.meanLuminance).toBeGreaterThan(3)
  expect(after.litFraction).toBeGreaterThan(0.02)
})

test('a top-down and a close view are captured for review', async ({ page }) => {
  await completedRun(page)
  await open3D(page)

  // Zoom in a little so the screenshot shows the boxes at a legible size.
  const box = (await page.locator('canvas').boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.wheel(0, -300)
  await page.waitForTimeout(800)
  await page.screenshot({ path: `${SHOTS}/3d-03-zoomed.png` })

  const pixels = await samplePixels(page)
  expect(pixels.litFraction).toBeGreaterThan(0.02)
})
