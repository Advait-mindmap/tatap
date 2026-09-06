/**
 * Uploading a document, and being told when it cannot be read.
 *
 * Found by direct testing: a real .docx fed to the file input produced NO reaction - no
 * extraction, no message. The message existed in the code; it was unreachable, because the
 * picker's `accept` list omitted .docx and so greyed it out, and dropping a file on the page hit
 * no handler at all. A user had no way to tell a rejected upload from a slow one.
 */

import { expect, test } from '@playwright/test'

import { apiReachable } from './support'

test.beforeAll(async () => {
  test.skip(!(await apiReachable()), 'no API on :8000 — start it with LLM_PROVIDER=stub')
})

const DOCX = 'e2e/fixtures/sample-brief.docx'

test('a Word document is read into the brief box', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('intake-screen')).toBeVisible()

  await page.getByTestId('file-input').setInputFiles(DOCX)

  await expect(page.getByTestId('intake-notice')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId('intake-error')).toHaveCount(0)

  // The point of returning text rather than a finished brief: it lands in the box, where the
  // reader can see what came out of their Word file and correct it.
  const text = await page.getByTestId('brief-input').inputValue()
  console.log(`  read ${text.length} characters from the .docx`)
  expect(text).toContain('30 MW Tier IV')
  expect(text).toContain('Chennai')
  expect(text).toContain('owner-furnished')

  // And it is usable: the extraction path runs on it like pasted text.
  await page.getByTestId('extract-button').click()
  await expect(page.getByTestId('confirm-screen')).toBeVisible({ timeout: 120_000 })
})

test('the file input accepts .docx and does not silently hide other formats', async ({ page }) => {
  await page.goto('/')
  const accept = (await page.getByTestId('file-input').getAttribute('accept')) ?? ''
  console.log(`  accept: ${accept}`)
  expect(accept, 'Word documents are still hidden by the picker').toContain('.docx')
  expect(accept, 'a PDF still cannot be chosen, so it cannot be explained').toContain('.pdf')
})

test('an unreadable file says why, and says what would work', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('intake-screen')).toBeVisible()

  await page.getByTestId('file-input').setInputFiles({
    name: 'rfp.pdf',
    mimeType: 'application/pdf',
    buffer: Buffer.from('%PDF-1.4 not really a pdf'),
  })

  const error = page.getByTestId('intake-error')
  await expect(error, 'an unreadable upload produced no message at all').toBeVisible({
    timeout: 30_000,
  })
  const message = (await error.textContent()) ?? ''
  console.log(`  message: ${message.slice(0, 110)}`)

  // Specific about this file, and about the way out. "Unsupported file type" alone would leave
  // the reader guessing which types are supported.
  expect(message).toMatch(/PDF text extraction is not implemented/i)
  expect(message, 'the message does not say what WOULD work').toContain('.docx')

  // The brief box is untouched, so nothing half-read is left behind pretending to be content.
  expect(await page.getByTestId('brief-input').inputValue()).toBe('')
})

test('a renamed file is refused on its content, not its name', async ({ page }) => {
  await page.goto('/')
  await page.getByTestId('file-input').setInputFiles({
    name: 'actually-a-zip.docx',
    mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    buffer: Buffer.from('PK not a real docx'),
  })

  const error = page.getByTestId('intake-error')
  await expect(error).toBeVisible({ timeout: 30_000 })
  expect(await error.textContent()).toMatch(/could not be opened|not a valid Word file/i)
})

test('dropping a file on the page works instead of doing nothing', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('intake-screen')).toBeVisible()

  // Dropping used to hit no handler at all - the single most silent path in the app.
  const buffer = (await import('node:fs')).readFileSync(DOCX)
  const dataTransfer = await page.evaluateHandle(async (bytes) => {
    const dt = new DataTransfer()
    dt.items.add(
      new File([new Uint8Array(bytes)], 'dropped-brief.docx', {
        type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      }),
    )
    return dt
  }, Array.from(buffer))

  await page.getByTestId('intake-panel').dispatchEvent('dragover', { dataTransfer })
  await page.getByTestId('intake-panel').dispatchEvent('drop', { dataTransfer })

  await expect(page.getByTestId('intake-notice')).toBeVisible({ timeout: 30_000 })
  const text = await page.getByTestId('brief-input').inputValue()
  console.log(`  dropped file read: ${text.length} characters`)
  expect(text).toContain('30 MW Tier IV')
})

test('dropping an unreadable file explains itself too', async ({ page }) => {
  await page.goto('/')
  const dataTransfer = await page.evaluateHandle(() => {
    const dt = new DataTransfer()
    dt.items.add(new File(['%PDF-1.4'], 'dropped.pdf', { type: 'application/pdf' }))
    return dt
  })

  await page.getByTestId('intake-panel').dispatchEvent('drop', { dataTransfer })
  await expect(page.getByTestId('intake-error')).toBeVisible({ timeout: 30_000 })
  expect(await page.getByTestId('intake-error').textContent()).toMatch(/PDF text extraction/i)
})
