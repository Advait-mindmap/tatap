import { useState } from 'react'
import type { ExtractedBrief, IntakeResult } from '../types'

interface Props {
  intake: IntakeResult
  onRun: (brief: ExtractedBrief) => void
  onBack: () => void
}

const FIELD_ORDER: (keyof ExtractedBrief)[] = [
  'project_name',
  'client',
  'city',
  'site_context',
  'in_dc_park_or_sez',
  'tier',
  'redundancy_topology',
  'it_load_mw',
  'scope',
  'power_position',
  'phasing',
  'target_rfs_date',
  'special_conditions',
]

function label(field: string): string {
  return field.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

/**
 * The question gate. The planner confirms what was extracted, sees the quote behind every field,
 * and fills anything intake could not find — before a single stage is reasoned about.
 *
 * Missing fields are editable here rather than merely listed: the whole point of asking is that
 * the answer changes the plan, so the answer has to be capturable.
 */
export function ConfirmScreen({ intake, onRun, onBack }: Props) {
  const [brief, setBrief] = useState<ExtractedBrief>(intake.brief)

  const set = (field: keyof ExtractedBrief, value: string) =>
    setBrief((current) => ({
      ...current,
      [field]:
        field === 'it_load_mw'
          ? value === ''
            ? null
            : Number(value)
          : value === ''
            ? null
            : value,
    }))

  const blocking = intake.questions.filter((q) => q.blocking)
  const stillMissing = blocking.filter((q) => {
    const value = brief[q.field as keyof ExtractedBrief]
    return value === null || value === undefined || value === ''
  })
  const modes = Object.entries(brief.delivery_mode_by_discipline ?? {})

  return (
    <div className="screen screen-wide" data-testid="confirm-screen">
      <div className="screen-inner">
        <div className="confirm-head">
          <div>
            <h1>Confirm the brief</h1>
            <p className="lede">
              Every value below was read out of your text and cites the phrase it came from.
              Anything intake could not find is blank and asked about — it is not guessed.
            </p>
          </div>
          <span className="badge mono">
            extraction confidence {intake.extraction_confidence_overall.toFixed(2)}
          </span>
        </div>

        {intake.warnings.length > 0 && (
          <div className="notice notice-warn">
            {intake.warnings.map((warning) => (
              <p key={warning} className="small">
                {warning}
              </p>
            ))}
          </div>
        )}

        {intake.flagged_conflicts.length > 0 && (
          <div className="notice notice-error">
            <strong>The brief contradicts itself — resolve before planning</strong>
            {intake.flagged_conflicts.map((conflict) => (
              <p key={conflict} className="small">
                {conflict}
              </p>
            ))}
          </div>
        )}

        <table className="fields" data-testid="extracted-fields">
          <thead>
            <tr>
              <th>Field</th>
              <th>Value</th>
              <th>Cited from your brief</th>
            </tr>
          </thead>
          <tbody>
            {FIELD_ORDER.map((field) => {
              const value = brief[field]
              const provenance = intake.field_provenance[field]
              const asked = intake.questions.find((q) => q.field === field)
              const empty = value === null || value === undefined || value === ''
              return (
                <tr key={field} className={empty ? 'is-missing' : undefined}>
                  <td className="field-name">{label(field)}</td>
                  <td>
                    <input
                      data-testid={`field-${field}`}
                      value={value === null || value === undefined ? '' : String(value)}
                      placeholder={asked ? 'not in the brief — please supply' : '—'}
                      onChange={(event) => set(field, event.target.value)}
                    />
                  </td>
                  <td className="cited">
                    {provenance ? (
                      <>
                        <span className="quote">“{provenance.quote}”</span>
                        <span className="chip mono">{provenance.confidence.toFixed(2)}</span>
                      </>
                    ) : asked ? (
                      <span className="muted small">{asked.why_needed}</span>
                    ) : (
                      <span className="muted small">—</span>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>

        {modes.length > 0 && (
          <section className="modes">
            <h3>Delivery mode by discipline</h3>
            <div className="mode-chips">
              {modes.map(([discipline, mode]) => (
                <span key={discipline} className="chip">
                  {discipline}: <strong>{mode}</strong>
                </span>
              ))}
            </div>
          </section>
        )}

        {intake.questions.length > 0 && (
          <section className="questions" data-testid="intake-questions">
            <h3>{intake.questions.length} thing(s) intake could not find</h3>
            <ul>
              {intake.questions.map((question) => (
                <li key={question.field}>
                  <strong>{question.question}</strong>
                  <span className="muted small"> {question.why_needed}</span>
                  {question.blocking && <span className="chip chip-alarm">blocking</span>}
                </li>
              ))}
            </ul>
          </section>
        )}

        <div className="row">
          <button className="ghost" onClick={onBack}>
            ← Edit the brief
          </button>
          <span className="grow" />
          {stillMissing.length > 0 && (
            <span className="muted small">
              {stillMissing.length} blocking field(s) still blank — the simulation will ask again
            </span>
          )}
          <button className="primary" onClick={() => onRun(brief)} data-testid="run-button">
            Run the simulation →
          </button>
        </div>
      </div>
    </div>
  )
}
