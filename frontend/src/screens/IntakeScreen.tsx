import { useRef, useState } from 'react'
import { ApiError, extractBrief, uploadDocument } from '../api'
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
  const [notice, setNotice] = useState('')
  const [uploading, setUploading] = useState(false)
  const [dragging, setDragging] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  /**
   * Read a dropped or chosen file.
   *
   * Everything goes to the server now, including plain text. Word files have to - a .docx is a
   * zip of XML and the browser cannot read one without a library - and routing .txt the same way
   * means there is ONE answer to "can this file be read", given by the code that actually reads
   * it, instead of a regex here that drifts from the server's list.
   *
   * The extracted text lands in the box rather than being submitted, so the reader can see what
   * came out of their document and correct it before anything is extracted from it.
   */
  async function onFile(file: File) {
    setError('')
    setDetail('')
    setUploading(true)
    try {
      const document = await uploadDocument(file)
      setFileName(document.filename)
      setText(document.text)
      setNotice(
        `Read ${document.characters.toLocaleString()} characters from ${document.filename}. ` +
          'Check it below before extracting.',
      )
    } catch (cause) {
      const err = cause as ApiError
      // The server says WHY - "PDF text extraction is not implemented", "that .docx could not be
      // opened" - so show that rather than a generic failure. Silence was the actual bug here:
      // an unsupported file used to do nothing at all.
      setNotice('')
      setError(err.message || `Could not read ${file.name}.`)
    } finally {
      setUploading(false)
    }
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
    <div className="screen intake" data-testid="intake-screen">
      <div className="screen-inner">
        <header className="intake-hero">
          <div className="intake-eyebrow mono">DC BUILD PLANNER</div>
          <h1>Start a data centre plan</h1>
          <p className="lede">
            Paste the brief — an email, an RFP extract, a basis-of-design note. Intake reads it
            into a structured brief, cites where each field came from, and asks about anything it
            cannot find. Nothing is assumed.
          </p>
          {/* What the thing actually does, in the order it does it. A reader who has never seen
              this should not have to press a button to find out. */}
          <ol className="intake-steps">
            <li><span className="intake-step-n mono">1</span> Read the brief, cite every field</li>
            <li><span className="intake-step-n mono">2</span> Simulate the build, stop at real decisions</li>
            <li><span className="intake-step-n mono">3</span> Draw it in 2D and 3D, export to P6</li>
          </ol>
        </header>

        <section
          className={`intake-panel${dragging ? ' is-dragging' : ''}`}
          data-testid="intake-panel"
          // Dropping a file used to do NOTHING - no handler, no message, no clue. That is worse
          // than refusing it, because the reader cannot tell the difference between "rejected"
          // and "still loading". Same path as the picker, so both give the same answer.
          onDragOver={(event) => {
            event.preventDefault()
            if (!dragging) setDragging(true)
          }}
          onDragLeave={(event) => {
            if (event.currentTarget.contains(event.relatedTarget as Node | null)) return
            setDragging(false)
          }}
          onDrop={(event) => {
            event.preventDefault()
            setDragging(false)
            const file = event.dataTransfer.files?.[0]
            if (file) void onFile(file)
          }}
        >
          <div className="intake-panel-head">
            <label className="intake-label" htmlFor="brief-input">The brief</label>
            <span className="muted small">plain text, or load a .docx / .md / .txt file</span>
          </div>

        <textarea
          id="brief-input"
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

        <div className="row intake-actions">
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
            accept=".md,.txt,.markdown,.csv,.json,.docx,.pdf,.doc,.rtf"
            style={{ display: 'none' }}
            data-testid="file-input"
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) void onFile(file)
            }}
          />
          <button
            className="ghost"
            onClick={() => fileInput.current?.click()}
            disabled={busy || uploading}
            data-testid="load-document"
          >
            {uploading ? 'Reading the document…' : 'Load a document…'}
          </button>
          {fileName && <span className="chip mono">{fileName}</span>}
          <span className="grow" />
          {text.trim() && <span className="muted small">{text.trim().length} characters</span>}
        </div>
        </section>

        {notice && !error && (
          <div className="notice notice-ok small" data-testid="intake-notice">
            {notice}
          </div>
        )}

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
