/**
 * Plain-language glosses for the app's own vocabulary.
 *
 * Two words leak from our internals into text a planner reads, and both arrive in the decision
 * card - "Full fragnet vs interface package" is the stated impact of the very first fork most
 * runs stop at. A civil engineer meeting the app for the first time has no reason to know
 * either, and a decision card that cannot be understood is a decision that cannot be made.
 *
 * A tooltip rather than a rewrite, deliberately. "Fragnet" is real scheduling vocabulary in the
 * planning world the product targets, and the library text using it was written by someone who
 * meant it; replacing it everywhere would flatten domain language the audience partly shares.
 * Marking it as explainable keeps the term and removes the barrier.
 */

import React from 'react'

export const GLOSSARY: Record<string, string> = {
  fragnet:
    'Fragment network — a small, reusable block of activities and their logic for one piece ' +
    'of work, which the engine copies into the plan wherever that work occurs.',
  fragnets:
    'Fragment networks — small, reusable blocks of activities and their logic, copied into ' +
    'the plan wherever that work occurs.',
  'interface package':
    'A subcontracted scope planned as its award, mobilisation and milestones rather than as ' +
    'the trade activities inside it — the detail sits with the subcontractor.',
  'interface packages':
    'Subcontracted scopes planned as award, mobilisation and milestones rather than as the ' +
    'trade activities inside them.',
  'long-lead':
    'Plant with a manufacturing and shipping time long enough to drive the programme — ' +
    'transformers, switchgear, chillers.',
  'hold point':
    'A point where work stops for an inspection or approval before it may continue.',
  'hold points':
    'Points where work stops for an inspection or approval before it may continue.',
}

/** Longest first, so "interface packages" matches before "interface package". */
const TERMS = Object.keys(GLOSSARY).sort((a, b) => b.length - a.length)

const PATTERN = new RegExp(
  `\\b(${TERMS.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})\\b`,
  'gi',
)

/**
 * Render text with any glossary term marked and explained on hover/focus.
 *
 * Returns nodes rather than HTML: the text comes from library data and from a language model,
 * and building markup from either would be an injection waiting to happen.
 */
export function withGlossary(text: string): React.ReactNode[] {
  if (!text) return []
  const nodes: React.ReactNode[] = []
  let cursor = 0
  let match: RegExpExecArray | null
  PATTERN.lastIndex = 0

  while ((match = PATTERN.exec(text)) !== null) {
    if (match.index > cursor) nodes.push(text.slice(cursor, match.index))
    const term = match[1]
    const gloss = GLOSSARY[term.toLowerCase()]
    nodes.push(
      <abbr
        key={`${match.index}-${term}`}
        className="glossary-term"
        title={gloss}
        data-testid="glossary-term"
        data-term={term.toLowerCase()}
        tabIndex={0}
      >
        {term}
      </abbr>,
    )
    cursor = match.index + term.length
  }
  if (cursor < text.length) nodes.push(text.slice(cursor))
  return nodes
}
