import { Handle, Position, type NodeProps } from 'reactflow'
import { useHighlight } from '../highlightContext'
import { KIND_STYLES } from '../nodeKinds'
import type { FlowNode } from '../types'

/**
 * `data` carries ONLY the node itself, and never changes for the life of the node. Hover and
 * selection arrive through context — see highlightContext.ts for why putting them in `data`
 * broke hovering outright.
 */
export interface CardData {
  node: FlowNode
}

/**
 * One node on the canvas.
 *
 * Carries the governance the reasoning attached rather than just a label: an unverified
 * dependency or a Tier-1 safety flag is exactly what a reviewer is scanning for, and burying it
 * one click deep in the detail panel would mean it is never seen.
 */
export function FlowNodeCard({ data }: NodeProps<CardData>) {
  const { node } = data
  const state = useHighlight()

  const highlighted = state.path.has(node.id)
  const dimmed = state.hovered !== null && !highlighted
  const direct = state.direct.has(node.id) || node.id === state.hovered
  const selected = node.id === state.selected

  const style = KIND_STYLES[node.kind]
  const isOpenFork = node.kind === 'decision_point' && node.status === 'open'
  const restsOnEstimates = (node.unverified_dependencies?.length ?? 0) > 0

  const classes = [
    'node-card',
    `kind-${node.kind}`,
    dimmed ? 'is-dimmed' : '',
    highlighted ? 'is-highlighted' : '',
    direct ? 'is-direct' : '',
    selected ? 'is-selected' : '',
    isOpenFork ? 'is-open-fork' : '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div
      className={classes}
      data-testid="node-card"
      data-node-id={node.id}
      data-kind={node.kind}
      data-stage={node.stage}
      data-dimmed={dimmed ? 'true' : 'false'}
      data-highlighted={highlighted ? 'true' : 'false'}
      data-selected={selected ? 'true' : 'false'}
      style={{
        width: style.width,
        background: style.fill,
        borderColor: selected || direct ? style.accent : style.border,
      }}
    >
      <Handle type="target" position={Position.Left} />
      <div className="node-head">
        <span className="node-glyph" style={{ color: style.accent }}>
          {style.glyph}
        </span>
        <span className="node-label" data-testid="node-label">{node.label}</span>
      </div>

      <div className="node-meta">
        {node.wbs_id && <span className="chip mono">{node.wbs_id}</span>}
        {node.dept && <span className="chip">{node.dept}</span>}
        {typeof node.duration_days === 'number' && node.duration_days > 0 && (
          <span className="chip">{node.duration_days}d</span>
        )}
        {node.kind === 'decision_point' && (
          <span className={`chip ${node.status === 'open' ? 'chip-alarm' : 'chip-resolved'}`}>
            {node.status === 'open' ? 'AWAITING ANSWER' : 'answered'}
          </span>
        )}
        {node.blocks_export && (
          <span className="chip chip-alarm" data-testid="badge-tier1">
            Tier-1 · blocks export
          </span>
        )}
        {restsOnEstimates && !node.blocks_export && (
          <span
            className="chip chip-warn"
            data-testid="badge-unverified"
            title={node.unverified_dependencies?.join(', ')}
          >
            unverified data
          </span>
        )}
        {typeof node.confidence === 'number' && node.confidence > 0 && (
          <span className="chip mono">conf {node.confidence.toFixed(2)}</span>
        )}
      </div>

      <Handle type="source" position={Position.Right} />
    </div>
  )
}
