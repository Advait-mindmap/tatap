/**
 * The two fixes, against the DEPLOYED build in a real browser.
 *
 * Neither test starts a run, so neither spends credits or a run slot: one reopens a run that
 * already exists, the other only uploads documents.
 *
 *     E2E_BASE_URL=https://tatap-app-production.up.railway.app npx playwright test prod-verify
 */

import { expect, test } from '@playwright/test'

test.skip(!process.env.E2E_BASE_URL, 'deployment check; set E2E_BASE_URL to run it')

/** The run from the report: completed, and showing no export panel. */
const REPORTED_RUN = 'run-68501821c08143dbba450d62e3385e72'

test('production: reopening the reported run shows its plan and the P6 export', async ({
  page,
}) => {
  test.setTimeout(300_000)

  await page.goto(`/?run=${REPORTED_RUN}`)

  // Before the fix this sat on "Simulating…" forever with an empty canvas, because attach
  // called a finished run "started" and the client dropped the output that came with it.
  await expect(page.getByTestId('run-status')).toHaveText(/Simulation complete/i, {
    timeout: 120_000,
  })

  const state = await page.evaluate(() => {
    const text = document.body.innerText
    return {
      status: document.querySelector('[data-testid="run-status"]')?.textContent?.trim() ?? '',
      progress: document.querySelector('.run-progress')?.textContent?.trim() ?? '',
      nodes: document.querySelectorAll('[data-testid="node-card"]').length,
      exportPanel: !!document.querySelector('[data-testid="export-panel"]'),
      exportButton: !!document.querySelector('[data-testid="export-p6-button"]'),
      signoffBox: !!document.querySelector('[data-testid="export-signed-by"]'),
      mentionsP6: /\.xer/i.test(text),
      // The report searched every button for "export"; do the same rather than trusting a
      // testid that might exist while the control is unreachable.
      exportButtons: Array.from(document.querySelectorAll('button'))
        .map((b) => (b.textContent ?? '').trim())
        .filter((t) => /export/i.test(t)),
    }
  })

  console.log(`  status        : ${state.status}`)
  console.log(`  progress      : ${state.progress}`)
  console.log(`  node cards    : ${state.nodes}`)
  console.log(`  export panel  : ${state.exportPanel}   button: ${state.exportButton}`)
  console.log(`  buttons w/ "export": ${JSON.stringify(state.exportButtons)}`)

  expect(state.nodes, 'the reopened run drew no plan').toBeGreaterThan(20)
  expect(state.exportPanel, 'the export panel is still missing').toBe(true)
  expect(state.exportButtons.length, 'no button mentions export').toBeGreaterThan(0)
  expect(state.mentionsP6, 'the page never mentions .xer').toBe(true)

  // Tier-1 gates it, so the button waits for a name - exactly as it should.
  if (state.signoffBox) {
    await expect(page.getByTestId('export-p6-button')).toBeDisabled()
    await page.getByTestId('export-signed-by').fill('A. Mahajan (verification)')
    await expect(page.getByTestId('export-p6-button')).toBeEnabled()
    console.log('  Tier-1 sign-off gates the button, and a name releases it')
  }

  await page.screenshot({ path: 'e2e/screenshots/prod-verify-export.png' })
})

test('production: a Word document is read, and an unreadable one explains itself', async ({
  page,
}) => {
  test.setTimeout(180_000)

  await page.goto('/')
  await expect(page.getByTestId('intake-screen')).toBeVisible()

  const accept = (await page.getByTestId('file-input').getAttribute('accept')) ?? ''
  console.log(`  accept: ${accept}`)
  expect(accept).toContain('.docx')

  await page.getByTestId('file-input').setInputFiles('e2e/fixtures/sample-brief.docx')
  await expect(page.getByTestId('intake-notice')).toBeVisible({ timeout: 60_000 })
  const text = await page.getByTestId('brief-input').inputValue()
  console.log(`  .docx read: ${text.length} characters`)
  expect(text).toContain('30 MW Tier IV')

  // And the silent path: an unreadable file must say why.
  await page.reload()
  await page.getByTestId('file-input').setInputFiles({
    name: 'rfp.pdf',
    mimeType: 'application/pdf',
    buffer: Buffer.from('%PDF-1.4'),
  })
  await expect(page.getByTestId('intake-error')).toBeVisible({ timeout: 60_000 })
  const message = (await page.getByTestId('intake-error').textContent()) ?? ''
  console.log(`  .pdf refusal: ${message.slice(0, 100)}`)
  expect(message).toMatch(/PDF text extraction is not implemented/i)
  expect(message).toContain('.docx')

  await page.screenshot({ path: 'e2e/screenshots/prod-verify-upload.png' })
})
