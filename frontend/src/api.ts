/**
 * Client for the planner backend.
 *
 * The app talks to a real backend for everything a user does. The golden fixture in
 * backend/tests/golden/ is a TEST artefact and is deliberately not importable from here — a view
 * that can silently fall back to canned data is a view that will one day show canned data to a
 * client and call it a plan.
 */

import type { IntakeResult, SimulationEvent } from './types'

/**
 * Where the API lives.
 *
 * Empty means SAME ORIGIN, which is the deployed shape: FastAPI serves the built frontend, so
 * the UI and the API share a host and there is no cross-origin call to configure. In
 * development the two are split across ports, so `.env.development` sets VITE_API_BASE.
 *
 * Defaulting to localhost here would have shipped a build that called the developer's machine
 * from the user's browser.
 */
export const API_BASE = (
  (import.meta.env.VITE_API_BASE as string | undefined) ?? ''
).replace(/\/$/, '')

export function wsUrl(path: string): string {
  if (API_BASE) return `${API_BASE.replace(/^http/, 'ws')}${path}`
  // Same origin: derive the scheme so https pages open wss, not ws (which browsers block).
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${scheme}://${window.location.host}${path}`
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: string,
  ) {
    super(message)
  }
}

/** POST /intake — free text (or an uploaded document's text) to a cited, structured brief. */
export async function extractBrief(
  text: string,
  sourceRef = 'pasted',
  attachments: string[] = [],
): Promise<IntakeResult> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}/intake`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, source_ref: sourceRef, attachments }),
    })
  } catch (cause) {
    throw new ApiError(
      `Could not reach the planner API at ${API_BASE || window.location.origin}. ` +
        'Is the backend running?',
      0,
      String(cause),
    )
  }

  if (!response.ok) {
    let detail = ''
    try {
      detail = (await response.json())?.detail ?? ''
    } catch {
      detail = await response.text().catch(() => '')
    }
    // 502 is the gateway failing, which is a different problem from a bad brief.
    const what =
      response.status === 502
        ? 'The extraction provider failed'
        : `Intake failed (HTTP ${response.status})`
    throw new ApiError(what, response.status, detail)
  }
  return (await response.json()) as IntakeResult
}

export interface SimulationSocket {
  answer(decisionPointId: string, answer: string): void
  stop(): void
  close(): void
}

/**
 * Open /ws/simulate and stream a run.
 *
 * The socket stays open across a halt: stop-and-ask is a pause, not an end, and the answer goes
 * back down the same connection so the backend resumes the run it already has rather than
 * starting a new one.
 */
export function runSimulation(
  brief: Record<string, unknown>,
  handlers: {
    onEvent: (event: SimulationEvent) => void
    onError: (message: string) => void
    onClose?: () => void
  },
  options: { attachRunId?: string } = {},
): SimulationSocket {
  const socket = new WebSocket(wsUrl('/ws/simulate'))

  socket.addEventListener('open', () => {
    // Re-attach rather than restart when we already have a run. The backend stores runs, so an
    // interrupted simulation is resumed from where it stopped — starting over would re-reason
    // every completed stage and charge for it a second time.
    socket.send(
      options.attachRunId
        ? JSON.stringify({ action: 'attach', run_id: options.attachRunId })
        : JSON.stringify({ action: 'start', brief }),
    )
  })

  socket.addEventListener('message', (message) => {
    try {
      handlers.onEvent(JSON.parse(message.data as string) as SimulationEvent)
    } catch (cause) {
      handlers.onError(`Malformed event from the server: ${String(cause)}`)
    }
  })

  socket.addEventListener('error', () => {
    handlers.onError(
      `Lost the connection to ${wsUrl('/ws/simulate')}. The run may still exist on the server.`,
    )
  })

  socket.addEventListener('close', () => handlers.onClose?.())

  const send = (payload: Record<string, unknown>) => {
    if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(payload))
  }

  return {
    answer: (decisionPointId, answer) =>
      send({ action: 'answer', decision_point_id: decisionPointId, answer }),
    stop: () => send({ action: 'stop' }),
    close: () => socket.close(),
  }
}

/** What the server can read. Kept as a fallback only — the list itself comes from the API. */
export const FALLBACK_FORMATS = ['.csv', '.docx', '.json', '.markdown', '.md', '.txt']

export interface ExtractedDocument {
  filename: string
  text: string
  characters: number
  lines: number
  /**
   * Non-empty when the file was only PARTLY readable — a PDF with scanned pages, say.
   *
   * This is the outcome worth being loud about: a half-read RFP reads plausibly and nobody
   * re-checks a file they think already worked. An outright failure is safer, because it stops.
   */
  notice?: string
}

/**
 * POST /intake/document — a document in, its readable text out.
 *
 * The text comes back rather than a finished brief on purpose: the reader sees what was pulled
 * out of their Word file, in the box, and can fix it before anything is extracted. A parser that
 * fed a half-read RFP straight into the reasoning would be worse than one that refused the file.
 */
export async function uploadDocument(file: File): Promise<ExtractedDocument> {
  const body = new FormData()
  body.append('file', file, file.name)

  let response: Response
  try {
    response = await fetch(`${API_BASE}/intake/document`, { method: 'POST', body })
  } catch (cause) {
    throw new ApiError(
      `Could not reach the planner API at ${API_BASE || window.location.origin}.`,
      0,
      String(cause),
    )
  }

  if (!response.ok) {
    // The server's refusals are the useful ones - it says WHY a .pdf or a renamed .zip cannot
    // be read - so surface its words rather than a generic failure.
    let detail = `The server could not read ${file.name} (${response.status}).`
    try {
      const body = await response.json()
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      /* not JSON; keep the status line */
    }
    throw new ApiError(detail, response.status, detail)
  }

  return (await response.json()) as ExtractedDocument
}

