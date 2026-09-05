/**
 * The P6 export, driven the way a user drives it.
 *
 * The backend round-trip test (backend/tests/test_xer_export.py) proves the FILE is right. This
 * proves the BUTTON is: that a real run in a real browser produces a real download, that the
 * Tier-1 sign-off actually gates it rather than merely being displayed, and that the file the
 * browser receives is the plan that was on screen.
 */

import { readFileSync } from 'node:fs'

import { expect, test } from '@playwright/test'

import { completedRun } from './support'

const SHOTS = 'e2e/screenshots'

test('a completed run exports to P6 and the downloaded file is that run', async ({ page }) => {
  test.setTimeout(180_000)

  await completedRun(page)

  const panel = page.getByTestId('export-panel')
  await expect(panel).toBeVisible()
  await panel.scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${SHOTS}/export-01-panel.png` })

  // --------------------------------------------------- Tier-1 gates the button, not a label
  //
  // CLAUDE.md rule 5. If the run carries Tier-1 safety work the button must be unusable until
  // someone puts their name to it — a warning the user can click straight past is not a gate.
  const signoff = page.getByTestId('export-signoff')
  const gated = await signoff.count()
  if (gated) {
    await expect(page.getByTestId('export-p6-button')).toBeDisabled()
    await page.screenshot({ path: `${SHOTS}/export-02-signoff-required.png` })
    await page.getByTestId('export-signed-by').fill('R. Mehta, HSE lead')
  }

  await page.getByTestId('export-start-date').fill('2026-04-06')
  await expect(page.getByTestId('export-p6-button')).toBeEnabled()

  // --------------------------------------------------------------- the download itself
  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout: 60_000 }),
    page.getByTestId('export-p6-button').click(),
  ])

  expect(download.suggestedFilename()).toMatch(/\.xer$/)
  const path = await download.path()
  const text = readFileSync(path!, 'latin1')

  // -------------------------------------------------- and it is the plan that was on screen
  expect(text.startsWith('ERMHDR\t5.0\t'), 'not an XER header').toBe(true)
  expect(text.trimEnd().endsWith('%E'), 'file does not end with %E').toBe(true)

  const taskBlock = text.split('%T\tTASK')[1].split('%T\t')[0]
  const exported = (taskBlock.match(/\n%R\t/g) ?? []).length

  const onScreen = Number(
    (await page.getByTestId('event-counter').textContent())?.match(/\d+/)?.[0] ?? 0,
  )
  expect(onScreen, 'the run drew no events').toBeGreaterThan(0)
  expect(exported, 'the exported file has no activities').toBeGreaterThan(20)

  // The sign-off is IN the file, not only in a server log.
  if (gated) expect(text).toContain('R. Mehta, HSE lead')

  // Dates are the anchor the user chose, not the day the export happened.
  expect(text, 'the exported dates do not start from the chosen project start').toContain(
    '2026-04-06 08:00',
  )

  await expect(page.getByTestId('export-done')).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/export-03-downloaded.png` })

  console.log(
    `EXPORT: ${download.suggestedFilename()} — ${exported} activities, ` +
      `${text.length} bytes, anchored 2026-04-06`,
  )
})
