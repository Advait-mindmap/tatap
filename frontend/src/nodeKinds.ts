/**
 * Visual identity per node kind.
 *
 * VISUALIZATION_SPEC.md section 1: "Node kinds are visually distinct... and decision point
 * (called out prominently — this is where thought stopped)." Decision points therefore get the
 * one saturated colour on the canvas, a heavier border and a status badge; everything else sits
 * in a cooler, quieter palette so a fork is findable at a glance in a dense graph.
 */

import type { NodeKind } from './types'

export interface KindStyle {
  label: string
  accent: string
  fill: string
  border: string
  /** Rendered before the label. Kept to shapes/symbols rather than emoji for print legibility. */
  glyph: string
  width: number
}

export const KIND_STYLES: Record<NodeKind, KindStyle> = {
  stage: {
    label: 'Stage',
    accent: '#94a3b8',
    fill: 'rgba(148, 163, 184, 0.10)',
    border: '#64748b',
    glyph: '▤',
    width: 238,
  },
  work_package: {
    label: 'Work package',
    accent: '#a78bfa',
    fill: 'rgba(167, 139, 250, 0.12)',
    border: '#7c5cd6',
    glyph: '▦',
    width: 228,
  },
  activity: {
    label: 'Activity',
    accent: '#38bdf8',
    fill: 'rgba(56, 189, 248, 0.10)',
    border: '#2b7fa8',
    glyph: '▸',
    width: 220,
  },
  milestone: {
    label: 'Milestone / delivery',
    accent: '#34d399',
    fill: 'rgba(52, 211, 153, 0.12)',
    border: '#2b8f6d',
    glyph: '◆',
    width: 220,
  },
  compliance_gate: {
    label: 'Compliance gate',
    accent: '#f472b6',
    fill: 'rgba(244, 114, 182, 0.12)',
    border: '#b3548a',
    glyph: '⛌',
    width: 220,
  },
  quality_hold: {
    label: 'Quality hold point',
    accent: '#fbbf24',
    fill: 'rgba(251, 191, 36, 0.12)',
    border: '#a37b16',
    glyph: '⏸',
    width: 212,
  },
  decision_point: {
    label: 'Decision point — thought stopped here',
    accent: '#fb7185',
    fill: 'rgba(251, 113, 133, 0.18)',
    border: '#fb7185',
    glyph: '◈',
    width: 258,
  },
}

/** Edge styling by kind: a delivery constraint should not look like ordinary fragnet logic. */
export const EDGE_STYLES: Record<string, { stroke: string; dash?: string; label: string }> = {
  fragnet: { stroke: '#475569', label: 'Fragnet logic' },
  hold_point: { stroke: '#a37b16', dash: '2 3', label: 'Quality hold' },
  cross_stage_gate: { stroke: '#f472b6', dash: '6 3', label: 'Cross-stage gate (IFC)' },
  delivery: { stroke: '#34d399', dash: '6 3', label: 'Delivery constraint' },
  compliance: { stroke: '#f472b6', dash: '6 3', label: 'Compliance' },
}

export const KIND_ORDER: NodeKind[] = [
  'stage',
  'work_package',
  'activity',
  'milestone',
  'compliance_gate',
  'quality_hold',
  'decision_point',
]
