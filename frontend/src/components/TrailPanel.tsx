import type { DecisionRecord, FlowNode, TrailEntry } from '../types'
import { KIND_STYLES } from '../nodeKinds'

interface Props {
  node: FlowNode | null
  trail: TrailEntry | null
  decision: DecisionRecord | null
  onClose: () => void
}

/**
 * Click-to-open reasoning trail.
 *
 * VISUALIZATION_SPEC.md section 1: "Click opens the reasoning trail for that node (what, why,
 * cited real-execution source, confidence, who decided it)."
 *
 * Two things are shown that a naive viewer would omit, because they are the difference between a
 * defensible plan and a plausible one: the CITED SOURCES, and the gap between the confidence the
 * reasoner claimed and the capped value actually used when the conclusion rests on unverified
 * library data.
 */
export function TrailPanel({ node, trail, decision, onClose }: Props) {
  if (!node) {
    return (
      <aside className="panel panel-empty" data-testid="trail-panel-empty">
        <h2>Reasoning trail</h2>
        <p className="muted">
          Click any node to see what it is, why it is there, what precedent it cites, and how
          confident that reasoning is.
        </p>
        <p className="muted small">
          Hover a node to light up everything upstream and downstream of it.
        </p>
      </aside>
    )
  }

  const style = KIND_STYLES[node.kind]
  const capped =
    trail?.stated_confidence != null && trail.stated_confidence > trail.confidence

  return (
    <aside className="panel" data-testid="trail-panel">
      <div className="panel-head">
        <div>
          <span className="panel-kind" style={{ color: style.accent }}>
            {style.glyph} {style.label}
          </span>
          <h2 data-testid="trail-title">{node.label}</h2>
        </div>
        <button className="close" onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>

      <dl className="facts">
        <dt>Stage</dt>
        <dd>{node.stage || '—'}</dd>
        {node.wbs_id && (
          <>
            <dt>WBS</dt>
            <dd className="mono">{node.wbs_id}</dd>
          </>
        )}
        {node.dept && (
          <>
            <dt>Department</dt>
            <dd>{node.dept}</dd>
          </>
        )}
        {typeof node.duration_days === 'number' && (
          <>
            <dt>Duration</dt>
            <dd>{node.duration_days} days</dd>
          </>
        )}
        {node.zone_id && (
          <>
            <dt>Zone (3D)</dt>
            <dd className="mono">{node.zone_id}</dd>
          </>
        )}
        <dt>Dates</dt>
        <dd className="muted">Not scheduled yet — CPM dates come with the P6 task.</dd>
      </dl>

      {node.kind === 'decision_point' && (
        <section className={node.status === 'open' ? 'block block-alarm' : 'block block-ok'}>
          <h3>{node.status === 'open' ? 'Awaiting an answer' : 'Answered'}</h3>
          {node.why_stuck && (
            <>
              <h4>Why the flow of thought stopped</h4>
              <p>{node.why_stuck}</p>
            </>
          )}
          {node.options && node.options.length > 0 && (
            <>
              <h4>Options</h4>
              <ul>
                {node.options.map((option) => (
                  <li key={option}>{option}</li>
                ))}
              </ul>
            </>
          )}
          {node.impact && (
            <>
              <h4>Impact</h4>
              <p>{node.impact}</p>
            </>
          )}
          {decision?.answer && (
            <>
              <h4>Answer</h4>
              <p className="answer">{decision.answer}</p>
            </>
          )}
        </section>
      )}

      {trail ? (
        <>
          <section className="block">
            <h3>Why this is here</h3>
            <p data-testid="trail-why">{trail.why}</p>
          </section>

          <section className="block">
            <h3>Cited sources</h3>
            {trail.sources.length ? (
              <ul className="sources" data-testid="trail-sources">
                {trail.sources.map((source) => (
                  <li key={source} className="mono">
                    {source}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">Nothing cited — this element is not auditable as precedent.</p>
            )}
          </section>

          <section className={capped ? 'block block-warn' : 'block'}>
            <h3>Confidence</h3>
            <p className="confidence" data-testid="trail-confidence">
              <strong>{trail.confidence.toFixed(2)}</strong>
              {capped && (
                <span className="muted" data-testid="trail-capped">
                  {' '}
                  — capped from {trail.stated_confidence!.toFixed(2)} claimed
                </span>
              )}
            </p>
            {capped && (
              <p className="small">
                This conclusion rests on library data no human has verified, so the confidence is
                the capped value rather than what the reasoner claimed. An estimate must not
                speak with the voice of a measurement.
              </p>
            )}
            {trail.unverified_dependencies.length > 0 && (
              <>
                <h4>Rests on unverified data</h4>
                <ul className="sources">
                  {trail.unverified_dependencies.map((dep) => (
                    <li key={dep} className="mono">
                      {dep}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </section>

          <dl className="facts">
            <dt>Decided by</dt>
            <dd>{trail.decided_by}</dd>
            <dt>HITL tier</dt>
            <dd>
              {trail.hitl_tier}
              {node.blocks_export && ' — blocks export until signed off'}
            </dd>
          </dl>
        </>
      ) : (
        <section className="block">
          <p className="muted">
            No reasoning-trail entry for this node. Structural nodes (stages, work packages) group
            the graph; their reasoning lives on the activities inside them.
          </p>
        </section>
      )}
    </aside>
  )
}
