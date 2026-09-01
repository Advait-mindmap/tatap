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
