import { useState } from 'react'
import type { OpenDecision, RunStatus } from '../types'
import type { RunState } from '../runState'

interface Props {
  run: RunState
  onAnswer: (decisionPointId: string, answer: string) => void
  onRestart: () => void
}

const STATUS_LABEL: Record<RunStatus, string> = {
  idle: 'Idle',
  running: 'Simulating…',
  halted: 'Stopped — needs your decision',
  complete: 'Simulation complete',
  error: 'Run failed',
}

/**
 * Live run controls, shown beside the graph while it draws.
 *
 * The decision prompt is the product's differentiator made visible (CLAUDE.md rule 3): when the
 * flow of thought cannot continue, it says WHY it is stuck, offers the options, states the
 * impact, and waits. It does not invent an answer, and it does not let the reader skip past it.
 */
export function RunPanel({ run, onAnswer, onRestart }: Props) {
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const decision: OpenDecision | undefined = run.openDecisions[0]

  return (
    <section className="run-panel" data-testid="run-panel">
      <h3>Run</h3>
      <p className={`run-status status-${run.status}`} data-testid="run-status">
        {STATUS_LABEL[run.status]}
      </p>

      <div className="run-progress">
        <span className="mono small">
          {run.stagesCompleted.length}/{run.stagesStarted.length} stages
        </span>
        <span className="mono small">{run.nodes.length} nodes</span>
        {run.currentStage && run.status === 'running' && (
          <span className="chip">{run.currentStage.replace(/_/g, ' ')}</span>
        )}
      </div>

      {run.status === 'error' && (
        <div className="notice notice-error small" data-testid="run-error">
          {run.error}
        </div>
      )}

      {decision && (
        <div className="prompt" data-testid="decision-prompt">
          <span className="prompt-tag">
            Decision needed · {decision.stage.replace(/_/g, ' ')}
          </span>
          <h4 data-testid="decision-question">{decision.question}</h4>

          <h5>Why it stopped here</h5>
          <p data-testid="decision-why">{decision.why_stuck}</p>

          {decision.impact && (
            <>
              <h5>What your answer changes</h5>
              <p>{decision.impact}</p>
            </>
          )}

          {decision.options.length > 0 && (
            <div className="options">
              {decision.options.map((option) => (
                <button
                  key={option}
                  className="option"
                  data-testid="decision-option"
                  onClick={() => onAnswer(decision.id, option)}
                >
                  {option}
                </button>
              ))}
            </div>
          )}

          <div className="own-answer">
            <input
              data-testid="decision-answer-input"
              placeholder="…or answer in your own words"
              value={drafts[decision.id] ?? ''}
              onChange={(event) =>
                setDrafts((current) => ({ ...current, [decision.id]: event.target.value }))
              }
              onKeyDown={(event) => {
                if (event.key === 'Enter' && (drafts[decision.id] ?? '').trim()) {
                  onAnswer(decision.id, drafts[decision.id].trim())
                }
              }}
            />
            <button
              className="primary small-btn"
              data-testid="decision-submit"
              disabled={!(drafts[decision.id] ?? '').trim()}
              onClick={() => onAnswer(decision.id, drafts[decision.id].trim())}
            >
              Answer
            </button>
          </div>

          {run.openDecisions.length > 1 && (
            <p className="muted small">
              {run.openDecisions.length - 1} more decision(s) after this one.
            </p>
          )}
        </div>
      )}

      {run.answered.length > 0 && (
        <div className="answered" data-testid="answered-list">
          <h5>Answered</h5>
          <ul>
            {run.answered.map((entry) => (
              <li key={entry.id}>
                <span className="mono small">{entry.id}</span>
                <span className="answer-text">{entry.answer}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {(run.status === 'complete' || run.status === 'error') && (
        <button className="ghost full" onClick={onRestart} data-testid="new-brief-button">
          Start another brief
        </button>
      )}
    </section>
  )
}
