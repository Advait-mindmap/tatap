/**
 * A halted run must survive the tab that started it.
 *
 * Reported alongside the decision-desync bug: "there's no persistent run URL to reattach to, so
 * the run becomes orphaned". That was exactly right, and it was the more serious half. Runs have
 * been durable on the server for a while and the `attach` action has always existed - the client
 * even had an `attachRunId` option wired into its socket helper - but nothing ever put the run
 * id anywhere a browser could keep. It lived in React state, so a refresh discarded the only
 * handle to a run that was still sitting on the server, halted, waiting to be answered, with
 * every stage already reasoned and paid for.
 */

import { expect, test } from '@playwright/test'

import { CHENNAI_BRIEF, apiReachable } from './support'

test.beforeAll(async () => {
  test.skip(!(await apiReachable()), 'no API on :8000 — start it with LLM_PROVIDER=stub')
})

test('a halted run is recoverable by URL after a reload', async ({ page }) => {
  test.setTimeout(300_000)

  await page.goto('/')
  await page.getByTestId('brief-input').fill(CHENNAI_BRIEF)
  await page.getByTestId('extract-button').click()
  await expect(page.getByTestId('confirm-screen')).toBeVisible({ timeout: 120_000 })
  await page.getByTestId('run-button').click()

  // Wait for the first genuine fork.
  await expect(page.getByTestId('decision-prompt')).toBeVisible({ timeout: 180_000 })
  const question = await page.getByTestId('decision-question').textContent()

  // The id must be in the address bar by now - put there when the server issued it, not when
  // the draw caught up.
  const runId = new URL(page.url()).searchParams.get('run')
  console.log(`  run in the URL: ${runId}`)
  expect(runId, 'the run id is not in the URL, so there is no way back to this run').toBeTruthy()
  expect(runId).toMatch(/^run-/)

  // The reported scenario: reload. Before this, the app went back to the intake screen and the
  // halted run was orphaned on the server.
  await page.reload()

  await expect(
    page.getByTestId('decision-prompt'),
    'after reloading, the halted run did not come back',
  ).toBeVisible({ timeout: 120_000 })
  await expect(page.getByTestId('run-status')).toHaveText(/needs your decision/i)
  expect(
    await page.getByTestId('decision-question').textContent(),
    'a different fork came back than the one the run was halted on',
  ).toBe(question)

  // And it is answerable, which is the point - recovering a run you still cannot move is no
  // recovery at all.
  const options = page.getByTestId('decision-option')
  expect(await options.count(), 'the recovered fork has no options to choose').toBeGreaterThan(0)
  await options.first().click()
  await expect(page.getByTestId('run-status')).not.toHaveText(/needs your decision/i, {
    timeout: 60_000,
  })
})

test('a run opened from a shared link picks up where it was', async ({ page, context }) => {
  test.setTimeout(300_000)

  await page.goto('/')
  await page.getByTestId('brief-input').fill(CHENNAI_BRIEF)
  await page.getByTestId('extract-button').click()
  await expect(page.getByTestId('confirm-screen')).toBeVisible({ timeout: 120_000 })
  await page.getByTestId('run-button').click()
  await expect(page.getByTestId('decision-prompt')).toBeVisible({ timeout: 180_000 })

  const url = page.url()
  expect(new URL(url).searchParams.get('run')).toBeTruthy()

  // A different tab entirely - the link handed to a colleague, or reopened tomorrow.
  const second = await context.newPage()
  await second.goto(url)
  await expect(
    second.getByTestId('decision-prompt'),
    'the link did not reopen the run',
  ).toBeVisible({ timeout: 120_000 })
  await expect(second.getByTestId('run-status')).toHaveText(/needs your decision/i)
  await second.close()
})

test('starting another brief clears the run from the URL', async ({ page }) => {
  test.setTimeout(300_000)

  await page.goto('/')
  await page.getByTestId('brief-input').fill(CHENNAI_BRIEF)
  await page.getByTestId('extract-button').click()
  await expect(page.getByTestId('confirm-screen')).toBeVisible({ timeout: 120_000 })
  await page.getByTestId('run-button').click()
  await expect(page.getByTestId('decision-prompt')).toBeVisible({ timeout: 180_000 })
  expect(new URL(page.url()).searchParams.get('run')).toBeTruthy()

  // Answer through to the end so the "Start another brief" control appears.
  for (let i = 0; i < 40; i += 1) {
    const status = page.getByTestId('run-status')
    await expect(status).not.toHaveText(/Simulating/, { timeout: 120_000 })
    if (/Simulation complete/i.test((await status.textContent()) ?? '')) break
    const option = page.getByTestId('decision-option').first()
    if (await option.count()) await option.click()
    else break
  }

  const newBrief = page.getByTestId('new-brief-button')
  if (await newBrief.count()) {
    await newBrief.click()
    await expect(page.getByTestId('intake-screen')).toBeVisible()
    expect(
      new URL(page.url()).searchParams.get('run'),
      'the old run is still in the URL, so a reload would reopen it instead of the new brief',
    ).toBeNull()
  }
})
