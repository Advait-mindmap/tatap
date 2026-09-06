/**
 * A halted run must always show the fork it is halted on.
 *
 * Reported from automated browser testing: the header said "Stopped — needs your decision" and
 * "1 open decision point(s)", and the decision card was nowhere in the DOM - confirmed by
 * searching the whole page text, so not a paint problem. The report came with an honest caveat:
 * it was driven by fast programmatic clicks, so it might have been the speed rather than a bug.
 *
 * The two numbers come from different places, which is the reason to suspect it is real:
 *
 *   the badge  counts decision_point nodes with status 'open' in the AUTHORITATIVE output
 *   the card   renders from run.openDecisions, built from decision_needed events and pruned
 *              OPTIMISTICALLY by answer() before the server has confirmed anything
 *
 * So the invariant this pins is not about any particular click speed: whatever the two sources
 * say, they must not disagree about whether there is a fork to answer. A user staring at
 * "needs your decision" with nothing to decide has no way forward.
 */

import { expect, test, type Page } from '@playwright/test'

import { CHENNAI_BRIEF, apiReachable } from './support'

test.beforeAll(async () => {
  test.skip(!(await apiReachable()), 'no API on :8000 — start it with LLM_PROVIDER=stub')
})

interface Snapshot {
  status: string
  badgeForks: number
  hasCard: boolean
  optionCount: number
  bodyMentionsDecision: boolean
}

async function snapshot(page: Page): Promise<Snapshot> {
  return page.evaluate(() => {
    const text = document.body.innerText
    const badge = /(\d+) open decision point\(s\)/.exec(text)
    return {
      status: document.querySelector('[data-testid="run-status"]')?.textContent?.trim() ?? '',
      badgeForks: badge ? Number(badge[1]) : 0,
      hasCard: !!document.querySelector('[data-testid="decision-prompt"]'),
      optionCount: document.querySelectorAll('[data-testid="decision-option"]').length,
      // The report searched the full page text rather than trusting a selector; do the same.
      bodyMentionsDecision: /Decision needed/i.test(text),
    }
  })
}

/** The invariant: halted with forks outstanding means the card is on screen. */
function assertCoherent(s: Snapshot, when: string) {
  const halted = /needs your decision/i.test(s.status)
  if (!halted && s.badgeForks === 0) return
  expect(
    s.hasCard || !halted,
    `${when}: status is "${s.status}" and the badge says ${s.badgeForks} open fork(s), ` +
      `but there is no decision card in the DOM (options=${s.optionCount}, ` +
      `page mentions "Decision needed": ${s.bodyMentionsDecision}). ` +
      'A run that says it needs a decision must show the decision.',
  ).toBe(true)
}

test('answering fast never leaves a halted run with no decision on screen', async ({ page }) => {
  test.setTimeout(300_000)

  await page.goto('/')
  await page.getByTestId('brief-input').fill(CHENNAI_BRIEF)
  await page.getByTestId('extract-button').click()
  await expect(page.getByTestId('confirm-screen')).toBeVisible({ timeout: 120_000 })
  await page.getByTestId('run-button').click()

  // Answer every fork as fast as the DOM allows, the way the report did: a programmatic click
  // the instant an option exists, with no settling in between.
  for (let round = 0; round < 40; round += 1) {
    const status = page.getByTestId('run-status')
    await expect(status).not.toHaveText(/Simulating/, { timeout: 120_000 })
    const text = (await status.textContent()) ?? ''
    if (/Simulation complete/i.test(text)) break
    if (/failed/i.test(text)) throw new Error('the run failed')

    const before = await snapshot(page)
    assertCoherent(before, `round ${round}, before answering`)

    const option = page.getByTestId('decision-option').first()
    if (!(await option.count())) {
      // Halted with no option to click is the reported state exactly.
      assertCoherent(before, `round ${round}, halted with no options`)
      break
    }

    // Programmatic click, then look immediately - this is the window the report was in.
    await option.evaluate((el: HTMLElement) => el.click())
    await page.waitForTimeout(300)
    assertCoherent(await snapshot(page), `round ${round}, 300ms after answering`)

    // And once things have settled, which is where a transient desync would have healed.
    await page.waitForTimeout(1500)
    assertCoherent(await snapshot(page), `round ${round}, settled after answering`)
  }

  const final = await snapshot(page)
  console.log(`  final: status="${final.status}" badge=${final.badgeForks} ` +
    `card=${final.hasCard} options=${final.optionCount}`)
  assertCoherent(final, 'at the end of the run')
})
