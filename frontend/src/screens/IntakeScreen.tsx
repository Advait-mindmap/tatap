import { useRef, useState } from 'react'
import { ApiError, extractBrief } from '../api'
import type { IntakeResult } from '../types'

interface Props {
  onExtracted: (result: IntakeResult, rawText: string) => void
}

/**
 * The front door. Paste a brief or load a document, and intake reads it into a structured brief
 * with a citation per field (PRODUCT_SPEC.md section 3.1).
 */
export function IntakeScreen({ onExtracted }: Props) {
  const [text, setText] = useState('')
  const [fileName, setFileName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [detail, setDetail] = useState('')
  const fileInput = useRef<HTMLInputElement>(null)

  async function onFile(file: File) {
    // Read client-side and send the text. PDFs are not parsed here - the backend's intake takes
    // text, and pretending to read a PDF by posting its bytes would fail confusingly.
    if (!/\.(md|txt|markdown|csv|json)$/i.test(file.name)) {
      setError(
        `${file.name} is not a text document. Paste the brief instead, or upload .md/.txt — ` +
          'PDF and DOCX extraction is not implemented yet.',
      )
      return
    }
    setError('')
    setFileName(file.name)
    setText(await file.text())
  }

  async function submit() {
    if (!text.trim()) {
      setError('Paste a brief, or load a text document.')
      return
    }
    setBusy(true)
    setError('')
    setDetail('')
    try {
      const result = await extractBrief(text, fileName || 'pasted', fileName ? [fileName] : [])
      onExtracted(result, text)
    } catch (cause) {
      const err = cause as ApiError
      setError(err.message)
      setDetail(err.detail ?? '')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="screen" data-testid="intake-screen">
      <div className="screen-inner">
        <h1>Start a data centre plan</h1>
        <p className="lede">
          Paste the brief — an email, an RFP extract, a basis-of-design note. Intake reads it into
          a structured brief, cites where each field came from, and asks about anything it cannot
          find. Nothing is assumed.
        </p>

        <textarea
          data-testid="brief-input"
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder={
            "e.g. We're bidding a 30 MW Tier IV data centre in Chennai, brownfield plot inside " +
            'SIPCOT. 2N on electrical and cooling. We self-perform civil; MEP is turnkey. ' +
            'Transformers are owner-furnished. Single handover, RFS end of 2027.'
          }
          rows={14}
          spellCheck={false}
        />

        <div className="row">
          <button
            className="primary"
            onClick={submit}
            disabled={busy}
            data-testid="extract-button"
          >
            {busy ? 'Reading the brief…' : 'Extract the brief'}
          </button>

          <input
            ref={fileInput}
            type="file"
            accept=".md,.txt,.markdown,.csv,.json"
            style={{ display: 'none' }}
            data-testid="file-input"
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) void onFile(file)
            }}
          />
          <button className="ghost" onClick={() => fileInput.current?.click()} disabled={busy}>
            Load a document…
          </button>
          {fileName && <span className="chip mono">{fileName}</span>}
          <span className="grow" />
          {text.trim() && <span className="muted small">{text.trim().length} characters</span>}
        </div>

        {error && (
          <div className="notice notice-error" data-testid="intake-error">
            <strong>{error}</strong>
            {detail && <p className="small mono">{detail}</p>}
          </div>
        )}

        <p className="muted small footnote">
          Extraction runs against the configured LLM provider. Nothing here is canned: the brief
          you type is the brief that gets planned.
        </p>
      </div>
    </div>
  )
}
