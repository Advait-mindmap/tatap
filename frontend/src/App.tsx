import { useCallback, useRef, useState } from 'react'

import { runSimulation, type SimulationSocket } from './api'
import { FlowView } from './FlowView'
import { INITIAL_RUN, reduceEvent, type RunState } from './runState'
import { ConfirmScreen } from './screens/ConfirmScreen'
import { IntakeScreen } from './screens/IntakeScreen'
import { RunPanel } from './screens/RunPanel'
import type { ExtractedBrief, IntakeResult } from './types'
import './styles.css'

type Phase = 'intake' | 'confirm' | 'run'

/**
 * The planner app: brief -> cited extraction -> confirmation -> live simulation -> 2D flow.
 *
 * There is no fixture anywhere in this path. The graph renders what the backend actually built
 * for the brief that was typed; the golden SimulationOutput stays where it belongs, as a test
 * artefact the backend suite pins.
 */
export default function App() {
  const [phase, setPhase] = useState<Phase>('intake')
  const [intake, setIntake] = useState<IntakeResult | null>(null)
  const [brief, setBrief] = useState<ExtractedBrief | null>(null)
  const [run, setRun] = useState<RunState>(INITIAL_RUN)
  const socket = useRef<SimulationSocket | null>(null)

  const start = useCallback((confirmed: ExtractedBrief) => {
    setBrief(confirmed)
    setRun({ ...INITIAL_RUN, status: 'running' })
    setPhase('run')

    socket.current?.close()
    socket.current = runSimulation(confirmed as unknown as Record<string, unknown>, {
      onEvent: (event) => setRun((current) => reduceEvent(current, event)),
      onError: (message) =>
        setRun((current) =>
          // A socket error after the run finished is the server closing a spent connection,
          // not a failed simulation.
          current.status === 'complete'
            ? current
            : { ...current, status: 'error', error: message },
        ),
    })
  }, [])

  const answer = useCallback((decisionPointId: string, value: string) => {
    // Clear the prompt optimistically so the panel does not sit on an answered question while
    // the round trip completes; decision_resolved confirms it.
    //
    // Status only returns to running when the LAST open fork is answered. A stage can raise
    // several, and the backend deliberately waits for all of them before resuming - saying
    // "Simulating…" while it is still waiting on the next answer strands the user in front of a
    // progress message that will never progress.
    setRun((current) => {
      const remaining = current.openDecisions.filter((d) => d.id !== decisionPointId)
      return {
        ...current,
        status: remaining.length ? 'halted' : 'running',
        openDecisions: remaining,
      }
    })
    socket.current?.answer(decisionPointId, value)
  }, [])

  const restart = useCallback(() => {
    socket.current?.stop()
    socket.current?.close()
    socket.current = null
    setRun(INITIAL_RUN)
    setIntake(null)
    setBrief(null)
    setPhase('intake')
  }, [])

  if (phase === 'intake') {
    return (
      <IntakeScreen
        onExtracted={(result) => {
          setIntake(result)
          setPhase('confirm')
        }}
      />
    )
  }

  if (phase === 'confirm' && intake) {
    return <ConfirmScreen intake={intake} onRun={start} onBack={() => setPhase('intake')} />
  }

  const output = run.output
  return (
    <FlowView
      nodes={run.nodes}
      edges={run.edges}
      trail={output?.reasoning_trail ?? []}
      decisions={
        output?.decisions ??
        run.answered.map((entry) => ({
          id: entry.id,
          question: '',
          answer: entry.answer,
          impact: '',
        }))
      }
      quality={output?.quality ?? {}}
      meta={
        output?.project_meta ?? {
          project_name: brief?.project_name ?? 'Untitled project',
          city: brief?.city,
          tier: brief?.tier,
          it_load_mw: brief?.it_load_mw,
          redundancy_topology: brief?.redundancy_topology,
        }
      }
      aside={<RunPanel run={run} onAnswer={answer} onRestart={restart} />}
      // Also true once complete: the authoritative output REPLACES the streamed graph, so the
      // final node set needs fitting too. The effect keys on node count, so it fires when the
      // graph changes and stays quiet while a reader pans.
      autoFit={run.status !== 'idle'}
    />
  )
}
