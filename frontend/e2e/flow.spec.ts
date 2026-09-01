/**
 * End-to-end tests for the 2D process-flow view (VISUALIZATION_SPEC.md section 1).
 *
 * These run a real browser against the real golden SimulationOutput. That matters: the unit-level
 * checks in the backend prove the DATA is right, and a typecheck proves the code compiles, but
 * neither can tell you whether 49 nodes actually appear on a canvas, whether a hover really dims
 * the rest of the graph, or whether the capped-confidence detail is legible to a reviewer.
 *
 * Screenshots land in e2e/screenshots/ and are the point of the exercise as much as the
 * assertions are — they let the rendered flow be reviewed without opening a browser.
 */

import { expect, test, type Page } from '@playwright/test'

const SHOTS = 'e2e/screenshots'

/** Golden-fixture facts these tests pin. Kept here so a mismatch reads as an intentional change. */
const EXPECTED_NODES = 49
const EXPECTED_STAGES = 6
/** Nodes on the transitive path through 'Transformer placement and alignment'. */
const EXPECTED_HIGHLIGHTED = 8
const EXPECTED_KINDS = [
  'stage',
  'work_package',
  'activity',
  'milestone',
  'compliance_gate',
  'quality_hold',
  'decision_point',
]

async function ready(page: Page) {
  await page.goto('/')
  await page.waitForSelector('[data-testid="node-card"]')
  // React Flow's fitView animates; settle before measuring or shooting.
  await page.waitForTimeout(700)
}

/**
 * Zoom out to the whole programme.
 *
 * The view deliberately OPENS at a legible working zoom rather than fitting all 49 nodes, so a
 * test that needs to reach a node in a distant column has to zoom out first - exactly as a
 * planner would. Uses the app's own fit-view control rather than poking the viewport.
 */
async function fitAll(page: Page) {
  await page.locator('.react-flow__controls-fitview').click()
  await page.waitForTimeout(500)
}

/**
 * Hover a node by its label.
 *
 * Playwright's `.hover()` teleports the cursor in a single jump, and React Flow's
 * onNodeMouseEnter does not fire from that - the browser never generates the crossing sequence
 * React's synthetic mouseenter is built on. Moving in steps from a neutral point produces a real
 * traversal and the handler fires. Diagnosed empirically: a single move gave 0 highlighted
 * nodes, a stepped move gave 8.
 */
async function hoverNode(page: Page, label: string) {
  const node = page.locator('.react-flow__node').filter({ hasText: label }).first()
  await expect(node).toBeVisible()

  // Retry rather than assume one attempt lands. Pointer-event delivery timing differs between
  // platforms, and a single stepped move was already intermittently missing on one machine.
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const box = await node.boundingBox()
    if (!box) throw new Error(`no bounding box for node "${label}"`)
    await page.mouse.move(8, 8)
    await page.waitForTimeout(60)
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 12 })
    // Settle with a small move well inside the card: a final position on the rounding edge can
    // deliver a leave immediately after the enter.
    await page.mouse.move(box.x + box.width / 2 + 3, box.y + box.height / 2 + 3)
    await page.waitForTimeout(250)
    if (await page.locator('[data-highlighted="true"]').count()) return
  }
  throw new Error(`hovering "${label}" never produced a highlight`)
}

test.describe('2D process flow', () => {
  test('renders every node from the golden simulation across the stage columns', async ({
    page,
  }) => {
    await ready(page)

    const cards = page.locator('[data-testid="node-card"]')
    await expect(cards).toHaveCount(EXPECTED_NODES)

    // Six stage columns, taken from the data rather than hardcoded positions.
    const stages = await page.$$eval('[data-testid="node-card"]', (els) =>
      Array.from(new Set(els.map((e) => e.getAttribute('data-stage')))).filter(Boolean),
    )
    expect(stages).toHaveLength(EXPECTED_STAGES)

    // Columns must be laid out left to right, one x per stage, or the graph does not read as a
    // programme.
    const columns = await page.$$eval('.react-flow__node', (els) =>
      Array.from(
        new Set(
          els.map((e) => Math.round((e as HTMLElement).getBoundingClientRect().x / 25) * 25),
        ),
      ),
    )
    expect(columns.length).toBeGreaterThanOrEqual(EXPECTED_STAGES)

    // All seven node kinds visually present.
    const kinds = await page.$$eval('[data-testid="node-card"]', (els) =>
      Array.from(new Set(els.map((e) => e.getAttribute('data-kind')))),
    )
    expect(kinds.sort()).toEqual([...EXPECTED_KINDS].sort())

    await page.screenshot({ path: `${SHOTS}/01-full-flow.png`, fullPage: false })
  })

  test('no two cards overlap, so every node can be hovered', async ({ page }) => {
    // Regression guard. Cards ran 44-98px tall against a 70px row pitch, so 30 pairs overlapped.
    // Invisible at a glance, but the card painted on top steals the pointer from the one below,
    // which is how hover-to-highlight silently stopped working for half the graph.
    await ready(page)

    const overlaps = await page.$$eval('.react-flow__node', (els) => {
      const boxes = els.map((e) => {
        const r = (e as HTMLElement).getBoundingClientRect()
        return { x: Math.round(r.x), y: r.y, h: r.height }
      })
      const columns = new Map<number, typeof boxes>()
      for (const b of boxes) {
        if (!columns.has(b.x)) columns.set(b.x, [])
        columns.get(b.x)!.push(b)
      }
      let count = 0
      for (const [, column] of columns) {
        column.sort((a, b) => a.y - b.y)
        for (let i = 1; i < column.length; i += 1) {
          if (column[i].y < column[i - 1].y + column[i - 1].h) count += 1
        }
      }
      return count
    })
    expect(overlaps).toBe(0)
  })

  test('opens at a zoom where the node text is legible', async ({ page }) => {
    // Regression guard for the demo complaint: a plain fitView packed all 49 nodes in at ~0.31
    // and the labels became grey mush. The view now opens at a working zoom.
    await ready(page)

    const zoom = await page.evaluate(() => {
      const el = document.querySelector('.react-flow__viewport') as HTMLElement
      const match = /scale\(([\d.]+)\)/.exec(el.style.transform)
      return match ? Number(match[1]) : 0
    })
    expect(zoom).toBeGreaterThanOrEqual(0.6)

    // Card WIDTH comes from KIND_STYLES, so it is the same on every platform. Height is
    // content-driven and therefore font-driven - a Linux runner falls back to DejaVu Sans and
    // wraps differently - so asserting a pixel height here would just encode the developer's
    // machine. The zoom check above is the real legibility guarantee.
    const activity = page
      .locator('.react-flow__node')
      .filter({ has: page.locator('[data-kind="activity"]') })
      .first()
    const box = await activity.boundingBox()
    expect(box!.width).toBeGreaterThan(120)
    await expect(activity.locator('[data-testid="node-label"]')).toBeVisible()
  })

  test('governance badges are visible on the canvas, not buried in a panel', async ({ page }) => {
    await ready(page)

    // Top bar: export blocked by unsigned Tier-1 work.
    const exportBlocked = page.getByTestId('badge-export-blocked')
    await expect(exportBlocked).toBeVisible()
    await expect(exportBlocked).toContainText('Tier-1 unsigned')

    // And on the node cards themselves — a reviewer scanning the graph must see these without
    // clicking anything.
    await expect(page.getByTestId('badge-tier1').first()).toBeVisible()
    await expect(page.getByTestId('badge-unverified').first()).toBeVisible()

    const tier1 = await page.getByTestId('badge-tier1').count()
    const unverified = await page.getByTestId('badge-unverified').count()
    expect(tier1).toBeGreaterThan(0)
    expect(unverified).toBeGreaterThan(0)

    await page
      .locator('.topbar')
      .screenshot({ path: `${SHOTS}/04-governance-badges.png` })
  })

  test('hovering a node highlights its transitive path and dims the rest', async ({ page }) => {
    await ready(page)

    // Nothing is dimmed before the hover.
    expect(await page.locator('[data-node-id][data-dimmed="true"]').count()).toBe(0)

    // A node mid-chain, so there is something both upstream and downstream of it.
    await hoverNode(page, 'Transformer placement')

    // Read both counts in ONE evaluate. Two separate count() calls can straddle a React
    // re-render, which made this assertion flake: the sum was measured across two different
    // frames. One atomic read, retried, removes the race rather than papering over it with a
    // longer sleep.
    await expect
      .poll(async () =>
        page.evaluate(() => ({
          dimmed: document.querySelectorAll('[data-dimmed="true"]').length,
          highlighted: document.querySelectorAll('[data-highlighted="true"]').length,
        })),
      )
      .toEqual({
        dimmed: EXPECTED_NODES - EXPECTED_HIGHLIGHTED,
        highlighted: EXPECTED_HIGHLIGHTED,
      })

    // The path is transitive, not one hop: the hint reports how many nodes are lit.
    const hint = page.getByTestId('hover-hint')
    await expect(hint).toBeVisible()
    await expect(hint).toContainText('upstream and downstream')

    await page.screenshot({ path: `${SHOTS}/02-hover-highlight.png` })
  })

  test('the highlighted path includes the delivery gate that constrains the activity', async ({
    page,
  }) => {
    await ready(page)

    await hoverNode(page, 'Transformer placement')

    // "Tie construction to delivery": the transformer delivery milestone is upstream of its
    // installation, so hovering the install must light the milestone.
    const deliveryGate = page.locator(
      '[data-node-id="gate.delivery-lead-transformer-hv"]',
    )
    await expect(deliveryGate).toHaveAttribute('data-highlighted', 'true')
  })

  test('clicking a node opens its reasoning trail with the capped-confidence detail', async ({
    page,
  }) => {
    await ready(page)

    await expect(page.getByTestId('trail-panel-empty')).toBeVisible()
    await fitAll(page)

    await page
      .locator('[data-testid="node-card"][data-kind="activity"]')
      .filter({ hasText: 'Transformer placement' })
      .first()
      .click()

    const panel = page.getByTestId('trail-panel')
    await expect(panel).toBeVisible()
    await expect(page.getByTestId('trail-title')).toContainText('Transformer placement')

    // What, why, cited source, confidence, who decided it.
    await expect(page.getByTestId('trail-why')).not.toBeEmpty()
    await expect(page.getByTestId('trail-sources')).toBeVisible()
    await expect(panel).toContainText('Decided by')

    // The anti-laundering detail: the capped value AND the gap from what was claimed.
    const capped = page.getByTestId('trail-capped')
    await expect(capped).toBeVisible()
    await expect(capped).toContainText('capped from')
    await expect(panel).toContainText('0.50')
    await expect(panel).toContainText('must not')

    await page.screenshot({ path: `${SHOTS}/03-trail-open.png` })
    await panel.screenshot({ path: `${SHOTS}/05-trail-panel.png` })
  })

  test('clicking a decision point shows why thought stopped and the answer given', async ({
    page,
  }) => {
    await ready(page)

    await fitAll(page)
    const fork = page.locator('[data-testid="node-card"][data-kind="decision_point"]').first()
    await expect(fork).toBeVisible()
    await fork.click()

    const panel = page.getByTestId('trail-panel')
    await expect(panel).toBeVisible()
    await expect(panel).toContainText('Answered')
    // The answer alone is not auditable: a resolved fork must still say why it was a fork.
    await expect(panel).toContainText('Why the flow of thought stopped')
    await expect(panel).toContainText('Options')
    await expect(panel).toContainText('Impact')

    await page.screenshot({ path: `${SHOTS}/06-decision-point.png` })
  })

  test('collapsing a stage removes its nodes, so a large graph stays navigable', async ({
    page,
  }) => {
    await ready(page)
    const before = await page.locator('[data-testid="node-card"]').count()

    await page.locator('.sidebar button', { hasText: 'commissioning' }).first().click()
    await page.waitForTimeout(300)

    const after = await page.locator('[data-testid="node-card"]').count()
    expect(after).toBeLessThan(before)
    expect(await page.locator('[data-stage="commissioning"]').count()).toBe(0)
  })
  test('captures a readable close-up of the graph', async ({ page }) => {
    // The fit-to-view shot proves the whole programme renders, but at that zoom the node text
    // is unreadable — and a screenshot nobody can read is not evidence of anything. This zooms
    // in so the cards, chips and governance badges are legible.
    await ready(page)

    const zoomIn = page.locator('.react-flow__controls-zoomin')
    for (let i = 0; i < 4; i += 1) {
      await zoomIn.click()
      await page.waitForTimeout(120)
    }
    await page.waitForTimeout(400)

    await expect(page.locator('[data-testid="node-card"]').first()).toBeVisible()
    await page.getByTestId('canvas').screenshot({ path: `${SHOTS}/07-readable-detail.png` })
  })

  test('captures an open decision point close-up', async ({ page }) => {
    await ready(page)

    await fitAll(page)
    const zoomIn = page.locator('.react-flow__controls-zoomin')
    for (let i = 0; i < 2; i += 1) {
      await zoomIn.click()
      await page.waitForTimeout(120)
    }
    // Forks sit directly under their stage header, so they are reachable once zoomed.
    await page.locator('[data-testid="node-card"][data-kind="decision_point"]').first().click()
    await page.waitForTimeout(400)
    await page.screenshot({ path: `${SHOTS}/08-decision-detail.png` })
  })
})
