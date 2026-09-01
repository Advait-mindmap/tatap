/**
 * Hover-to-highlight: "the specific flows tied to it".
 *
 * VISUALIZATION_SPEC.md section 1: resting on a node highlights its predecessors and successors,
 * the path through it, and its compliance gates and material links; everything else dims.
 *
 * The traversal is TRANSITIVE in both directions, not one hop. One hop would answer "what
 * touches this?", which a planner can already see. The question they actually have is "what does
 * this hold up, and what is holding it up" — that is the whole upstream and downstream chain.
 */

import type { FlowEdge } from './types'

export interface Highlight {
  /** Every node on the path through the hovered node, including itself. */
  nodes: Set<string>
  edges: Set<string>
  /** Immediate neighbours, drawn more strongly than the wider chain. */
  direct: Set<string>
  upstream: Set<string>
  downstream: Set<string>
}

export const EMPTY_HIGHLIGHT: Highlight = {
  nodes: new Set(),
  edges: new Set(),
  direct: new Set(),
  upstream: new Set(),
  downstream: new Set(),
}

export function edgeId(edge: FlowEdge): string {
  return `${edge.from}->${edge.to}:${edge.kind}`
}

interface Adjacency {
  out: Map<string, FlowEdge[]>
  in: Map<string, FlowEdge[]>
}

export function buildAdjacency(edges: FlowEdge[]): Adjacency {
  const out = new Map<string, FlowEdge[]>()
  const incoming = new Map<string, FlowEdge[]>()
  for (const edge of edges) {
    if (!out.has(edge.from)) out.set(edge.from, [])
    out.get(edge.from)!.push(edge)
    if (!incoming.has(edge.to)) incoming.set(edge.to, [])
    incoming.get(edge.to)!.push(edge)
  }
  return { out, in: incoming }
}

function walk(
  start: string,
  adjacency: Adjacency,
  direction: 'up' | 'down',
  nodes: Set<string>,
  edges: Set<string>,
): void {
  const stack = [start]
  const visited = new Set<string>([start])
  while (stack.length) {
    const current = stack.pop()!
    const links = direction === 'down' ? adjacency.out.get(current) : adjacency.in.get(current)
    for (const edge of links ?? []) {
      edges.add(edgeId(edge))
      const next = direction === 'down' ? edge.to : edge.from
      nodes.add(next)
      if (!visited.has(next)) {
        visited.add(next)
        stack.push(next)
      }
    }
  }
}

export function computeHighlight(nodeId: string | null, adjacency: Adjacency): Highlight {
  if (!nodeId) return EMPTY_HIGHLIGHT

  const nodes = new Set<string>([nodeId])
  const edges = new Set<string>()
  const upstream = new Set<string>()
  const downstream = new Set<string>()

  walk(nodeId, adjacency, 'up', upstream, edges)
  walk(nodeId, adjacency, 'down', downstream, edges)
  upstream.forEach((id) => nodes.add(id))
  downstream.forEach((id) => nodes.add(id))

  const direct = new Set<string>()
  for (const edge of adjacency.out.get(nodeId) ?? []) direct.add(edge.to)
  for (const edge of adjacency.in.get(nodeId) ?? []) direct.add(edge.from)

  return { nodes, edges, direct, upstream, downstream }
}
