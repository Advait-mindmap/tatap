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
  expect(accept, 'a PDF cannot be chosen').toContain('.pdf')
  expect(accept, 'a .doc cannot be chosen, so it cannot be explained').toContain('.doc')
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
  // the reader guessing. PDFs are read now, so the reason is that THIS one will not open -
  // not that the format is refused.
  expect(message).toMatch(/could not be opened/i)

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
  expect(await page.getByTestId('intake-error').textContent()).toMatch(/could not be opened/i)
})


/**
 * A real PDF, built in the browser: a byte-accurate xref, a content stream per page, a font
 * resource. Pages given an empty string get no content stream, which is what a scanned page
 * looks like to an extractor.
 *
 * Built here rather than committed as a binary so the fixture is readable and reviewable, and
 * so a change to it is a diff rather than an opaque blob.
 */
function makePdf(pages: string[]): Buffer {
  const objects: string[] = ['', '', '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
  const PAGES = 2
  const FONT = 3
  const kids: number[] = []

  for (const text of pages) {
    let contents = ''
    if (text) {
      const escaped = text.replace(/\\/g, '\\\\').replace(/\(/g, '\\(').replace(/\)/g, '\\)')
      const stream = `BT /F1 12 Tf 72 720 Td (${escaped}) Tj ET`
      objects.push(`<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`)
      contents = ` /Contents ${objects.length} 0 R`
    }
    objects.push(
      `<< /Type /Page /Parent ${PAGES} 0 R /MediaBox [0 0 612 792]${contents}` +
        ` /Resources << /Font << /F1 ${FONT} 0 R >> >> >>`,
    )
    kids.push(objects.length)
  }

  objects[0] = `<< /Type /Catalog /Pages ${PAGES} 0 R >>`
  objects[1] =
    `<< /Type /Pages /Count ${kids.length} /Kids [${kids.map((k) => `${k} 0 R`).join(' ')}] >>`

  let out = '%PDF-1.4\n'
  const offsets: number[] = []
  objects.forEach((body, index) => {
    offsets.push(Buffer.byteLength(out))
    out += `${index + 1} 0 obj\n${body}\nendobj\n`
  })
  const start = Buffer.byteLength(out)
  out += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`
  for (const offset of offsets) out += `${String(offset).padStart(10, '0')} 00000 n \n`
  out += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${start}\n%%EOF\n`
  return Buffer.from(out, 'latin1')
}

test('a PDF brief is read into the box', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('intake-screen')).toBeVisible()

  await page.getByTestId('file-input').setInputFiles({
    name: 'rfp.pdf',
    mimeType: 'application/pdf',
    buffer: makePdf([
      'We are bidding a 30 MW Tier IV data centre in Chennai.',
      'Topology is 2N and the scope is design-build.',
    ]),
  })

  await expect(page.getByTestId('intake-notice')).toBeVisible({ timeout: 30_000 })
  const text = await page.getByTestId('brief-input').inputValue()
  console.log(`  .pdf read: ${text.length} characters`)
  expect(text).toContain('30 MW Tier IV')
  expect(text).toContain('design-build')

  // Fully readable, so the notice is the ordinary one rather than a warning.
  expect(await page.getByTestId('intake-notice').getAttribute('data-partial')).toBe('false')
})

test('a partly scanned PDF warns instead of quietly handing over half a brief', async ({
  page,
}) => {
  await page.goto('/')
  await page.getByTestId('file-input').setInputFiles({
    name: 'scanned-rfp.pdf',
    mimeType: 'application/pdf',
    buffer: makePdf([
      'We are bidding a 30 MW Tier IV data centre in Chennai.',
      '',
      'Delivery is design-build with turnkey electrical.',
      '',
    ]),
  })

  const notice = page.getByTestId('intake-notice')
  await expect(notice).toBeVisible({ timeout: 30_000 })
  const message = (await notice.textContent()) ?? ''
  console.log(`  partial read: ${message.slice(0, 120)}`)

  expect(message).toContain('2 of 4 pages')
  // Styled as a warning, not as success. A half-read RFP shown in the same green as a whole one
  // is the silent failure again in a friendlier font.
  expect(await notice.getAttribute('data-partial')).toBe('true')
  expect(await page.getByTestId('brief-input').inputValue()).toContain('30 MW')
})

test('a scanned PDF with no text layer is refused, not returned empty', async ({ page }) => {
  await page.goto('/')
  await page.getByTestId('file-input').setInputFiles({
    name: 'scan.pdf',
    mimeType: 'application/pdf',
    buffer: makePdf(['', '', '']),
  })

  const error = page.getByTestId('intake-error')
  await expect(error).toBeVisible({ timeout: 30_000 })
  expect((await error.textContent()) ?? '').toMatch(/no text layer/i)
  // Nothing half-read left behind pretending to be content.
  expect(await page.getByTestId('brief-input').inputValue()).toBe('')
})
