/**
 * The two fixes, checked against the DEPLOYED build in a real browser.
 *
 * Runs only with E2E_BASE_URL set, and deliberately not in CI: it drives a real run against
 * production, which spends credits and counts against RUNS_PER_CLIENT_DAILY.
 *
 *     E2E_BASE_URL=https://tatap-app-production.up.railway.app npx playwright test prod-run-recovery
 */

import { expect, test } from '@playwright/test'

import { CHENNAI_BRIEF } from './support'

test.skip(!process.env.E2E_BASE_URL, 'deployment check; set E2E_BASE_URL to run it')

test('production: a run is addressable and a halt shows its decision', async ({ page }) => {
  test.setTimeout(900_000) // a real run reasons thirteen stages through the live provider

  await page.goto('/')
  await expect(page.getByTestId('intake-screen')).toBeVisible()

  // No run in the URL before one exists.
  expect(new URL(page.url()).searchParams.get('run')).toBeNull()

  await page.getByTestId('brief-input').fill(CHENNAI_BRIEF)
  await page.getByTestId('extract-button').click()
  await expect(page.getByTestId('confirm-screen')).toBeVisible({ timeout: 300_000 })
  await page.getByTestId('run-button').click()

  // ---------------------------------------------------------------- 1. addressable at once
  //
  // "Immediately" means as soon as the server issues the id, which is the first event of the
  // run - not when the graph finishes drawing. A run that dies mid-draw is exactly the one
  // worth being able to get back to.
  await expect
    .poll(async () => new URL(page.url()).searchParams.get('run'), { timeout: 120_000 })
    .toMatch(/^run-[0-9a-f]{32}$/)

  const runId = new URL(page.url()).searchParams.get('run')!
  console.log(`  run in the address bar: ${runId} (${runId.length - 4} hex chars)`)
  await page.screenshot({ path: 'e2e/screenshots/prod-recovery-01-url.png' })

  // ---------------------------------------------------------------- 2. a halt shows its fork
  //
  // The reported failure was a run that said "Stopped — needs your decision" and "N open
  // decision point(s)" with no card anywhere in the DOM. Checked here the way it was reported:
  // against the whole page text, not a selector that might merely be hidden.
  await expect(page.getByTestId('decision-prompt')).toBeVisible({ timeout: 600_000 })

  const atHalt = await page.evaluate(() => {
    const text = document.body.innerText
    return {
      status: document.querySelector('[data-testid="run-status"]')?.textContent?.trim() ?? '',
      badge: Number(/(\d+) open decision point\(s\)/.exec(text)?.[1] ?? 0),
      question: document.querySelector('[data-testid="decision-question"]')?.textContent ?? '',
      options: document.querySelectorAll('[data-testid="decision-option"]').length,
      pageMentionsDecisionNeeded: /Decision needed/i.test(text),
    }
  })
  console.log(`  at the halt: status="${atHalt.status}" badge=${atHalt.badge} ` +
    `options=${atHalt.options} question="${atHalt.question.slice(0, 60)}"`)

  expect(atHalt.status).toMatch(/needs your decision/i)
  expect(atHalt.question, 'the halt shows no question').not.toBe('')
  expect(atHalt.options, 'the decision has no answerable options').toBeGreaterThan(0)
  expect(atHalt.pageMentionsDecisionNeeded).toBe(true)
  // The two sources agree, which is the whole point of the fix.
  expect(atHalt.badge, 'the badge and the card disagree about open forks').toBeGreaterThan(0)
  await page.screenshot({ path: 'e2e/screenshots/prod-recovery-02-decision.png' })

  // ---------------------------------------------------------------- 3. and it survives a reload
  await page.reload()
  await expect(
    page.getByTestId('decision-prompt'),
    'reloading lost the halted run — it is orphaned on the server',
  ).toBeVisible({ timeout: 300_000 })
  expect(await page.getByTestId('decision-question').textContent()).toBe(atHalt.question)
  console.log('  survived a reload, same fork')
  await page.screenshot({ path: 'e2e/screenshots/prod-recovery-03-after-reload.png' })

  // And answerable afterwards - recovering a run you cannot move is no recovery.
  //
  // "Moved on" is NOT "the status stops saying it needs a decision". This brief raises forks at
  // most stages, so the run answers one and halts on the next, and the status text is identical
  // for a different question - an earlier version of this waited five minutes for text that
  // could never change. Verified over the wire meanwhile: the server accepts an answer on an
  // attach socket and emits decision_resolved, so the run does resume.
  //
  // What actually shows movement is the QUESTION changing, or the run finishing.
  await page.getByTestId('decision-option').first().click()
  await expect
    .poll(
      async () => {
        const status = (await page.getByTestId('run-status').textContent()) ?? ''
        if (/Simulation complete/i.test(status)) return 'complete'
        if (/failed/i.test(status)) return 'failed'
        return (await page.getByTestId('decision-question').textContent()) ?? ''
      },
      {
        timeout: 300_000,
        message: 'after answering the recovered fork the run did not move: same question, ' +
          'not complete, not failed',
      },
    )
    .not.toBe(atHalt.question)
  console.log('  answered the recovered fork; the run moved past it')
})
