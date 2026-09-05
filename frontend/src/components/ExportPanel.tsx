/**
 * Export a completed run to Primavera P6.
 *
 * Three things this panel has to get right, and only one of them is the download.
 *
 * 1. The plan is day offsets, not dates. The engine computes when each activity starts relative
 *    to day 0; it has no opinion about when day 0 is. So the reader supplies that, and the panel
 *    says so — rather than quietly stamping today's date on a programme and letting a planner
 *    discover the assumption in P6.
 * 2. Tier-1 safety work needs a named human before it leaves the building (CLAUDE.md rule 5).
 *    The server enforces it; this asks for the name and shows which activities are being
 *    accepted, so signing is a decision rather than a box to clear.
 * 3. It states what the export is proven to be and what it isn't. See EXPORT_NOTE below.
 */

import { useState } from 'react'

import { API_BASE } from '../api'
import type { SimulationOutput } from '../types'

interface Props {
  runId: string
  output: SimulationOutput
}

function today(): string {
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

export function ExportPanel({ runId, output }: Props) {
  const quality = (output.quality ?? {}) as Record<string, unknown>
  const blocked = Boolean(quality.export_blocked)
  const tier1 = (quality.tier_1_ids as string[] | undefined) ?? []

  const [startDate, setStartDate] = useState(today())
  const [signedBy, setSignedBy] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState('')

  const canExport = Boolean(startDate) && (!blocked || signedBy.trim().length > 0)

  async function download() {
    setBusy(true)
    setError('')
    setDone('')
    try {
      const params = new URLSearchParams({ start_date: startDate })
      if (signedBy.trim()) params.set('signed_by', signedBy.trim())
      const response = await fetch(`${API_BASE}/export/${runId}.xer?${params}`)

      if (!response.ok) {
        // The server's refusals are the interesting ones (unfinished run, missing sign-off),
        // so surface what it said rather than a generic failure.
        let detail = `Export failed (${response.status}).`
        try {
          const body = await response.json()
          const d = body.detail
          detail = typeof d === 'string' ? d : (d?.message ?? detail)
        } catch {
          /* not JSON; keep the status line */
        }
        setError(detail)
        return
      }

      const blob = await response.blob()
      const name =
        /filename="([^"]+)"/.exec(response.headers.get('content-disposition') ?? '')?.[1] ??
        `${runId}.xer`
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = name
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
      setDone(`${name} — ${output.activities.length} activities from day ${startDate}.`)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Export failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="export-panel" data-testid="export-panel">
      <h5>Export to Primavera P6</h5>

      <label className="export-field">
        <span className="small muted">Project start (day 0 of the plan)</span>
        <input
          type="date"
          value={startDate}
          data-testid="export-start-date"
          onChange={(event) => setStartDate(event.target.value)}
        />
      </label>
      <p className="muted small">
        The schedule is computed as offsets from day 0. Dates in the exported file are those
        offsets counted from the date above — nothing is estimated at export time.
      </p>

      {blocked && (
        <div className="notice notice-warn small" data-testid="export-signoff">
          <strong>{tier1.length} Tier-1 safety activities need a named sign-off</strong> before
          this plan can leave the tool.
          <ul className="mono small tier1-list">
            {tier1.slice(0, 6).map((id) => (
              <li key={id}>{id}</li>
            ))}
            {tier1.length > 6 && <li>…and {tier1.length - 6} more</li>}
          </ul>
          <input
            placeholder="Who is accepting these? (name and role)"
            value={signedBy}
            data-testid="export-signed-by"
            onChange={(event) => setSignedBy(event.target.value)}
          />
        </div>
      )}

      <button
        className="primary full"
        disabled={!canExport || busy}
        onClick={download}
        data-testid="export-p6-button"
      >
        {busy ? 'Building the file…' : 'Export to P6 (.xer)'}
      </button>

      {error && (
        <div className="notice notice-error small" data-testid="export-error">
          {error}
        </div>
      )}
      {done && (
        <div className="notice notice-ok small" data-testid="export-done">
          Downloaded {done}
        </div>
      )}

      <p className="muted small export-caveat" data-testid="export-caveat">
        <strong>What this is:</strong> a structurally valid XER whose table layout matches a real
        P6 export, checked by reading the file back with an independent parser and comparing every
        activity, date and dependency against this run.{' '}
        <strong>What it is not:</strong> confirmation that it imports cleanly into your specific
        P6 installation. That depends on the version and configuration at the far end, and can
        only be settled by importing it there.
      </p>
    </div>
  )
}
