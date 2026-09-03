import { createContext, useContext } from 'react'

/**
 * Highlight state, passed to node cards through context rather than through their `data` prop.
 *
 * WHY THIS EXISTS — it fixes a real interaction bug, not a style preference.
 *
 * React Flow takes `nodes` as a controlled array. Rebuilding that array on every hover (to push
 * `dimmed`/`highlighted` into each node's `data`) gives every node a new identity, so React Flow
 * re-creates the node elements. The card under the cursor is therefore replaced mid-hover, the
 * browser fires `mouseout`, the highlight clears, the array rebuilds again — and the pointer
 * oscillates between the card and the pane underneath it:
 *
 *     mouseover: node-card        mouseout: node-card (is-highlighted)
 *     mouseover: react-flow__pane mouseover: node-card (is-highlighted)  ... repeating
 *
 * The visible symptom is a highlight that flickers and never settles.
 *
 * Keeping `nodes` referentially stable and reading the transient state from context means the
 * inner card re-renders while the node element it lives in is left alone.
 */
export interface HighlightState {
  hovered: string | null
  selected: string | null
  /** Every node on the transitive path through the hovered node. */
  path: Set<string>
  /** Immediate neighbours, drawn more strongly than the wider chain. */
  direct: Set<string>
  /**
   * A zone highlighted from the OTHER view (VISUALIZATION_SPEC.md section 3, `highlight(ref)`).
   *
   * Separate from `hovered` because it arrives from elsewhere: the cursor is over a box in the
   * 3D model, not over a card. When set, the 2D view lights the activities that build that zone
   * and dims the rest, which is what makes the two views one instrument rather than two
   * pictures of the same data.
   */
  zone: string | null
}

export const EMPTY_STATE: HighlightState = {
  hovered: null,
  selected: null,
  path: new Set(),
  direct: new Set(),
  zone: null,
}

export const HighlightContext = createContext<HighlightState>(EMPTY_STATE)

export function useHighlight(): HighlightState {
  return useContext(HighlightContext)
}
