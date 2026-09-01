/**
 * Client for the planner backend.
 *
 * The app talks to a real backend for everything a user does. The golden fixture in
 * backend/tests/golden/ is a TEST artefact and is deliberately not importable from here — a view
 * that can silently fall back to canned data is a view that will one day show canned data to a
 * client and call it a plan.
 */

import type { IntakeResult, SimulationEvent } from './types'

/** Where the API lives. Same-origin in deployment; the Vite dev server proxies nothing. */
export const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, '') ??
  'http://localhost:8000'

export function wsUrl(path: string): string {
  const base = API_BASE.replace(/^http/, 'ws')
  return `${base}${path}`
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
      `Could not reach the planner API at ${API_BASE}. Is the backend running?`,
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
): SimulationSocket {
  const socket = new WebSocket(wsUrl('/ws/simulate'))

  socket.addEventListener('open', () => {
    socket.send(JSON.stringify({ action: 'start', brief }))
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
