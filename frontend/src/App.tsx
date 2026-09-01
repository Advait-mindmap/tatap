import { useCallback, useRef, useState } from 'react'

import { runSimulation, type SimulationSocket } from './api'
import { FlowView } from './FlowView'
import { View3D } from './components/View3D'
import { INITIAL_RUN, reduceEvent, type RunState } from './runState'
import { ConfirmScreen } from './screens/ConfirmScreen'
import { IntakeScreen } from './screens/IntakeScreen'
import { RunPanel } from './screens/RunPanel'
import type { ExtractedBrief, IntakeResult, SimulationEvent } from './types'
import { parseZone, STAGE_COLORS, type Zone3D } from './viz3d'
import './styles.css'

type Phase = 'intake' | 'confirm' | 'run'
type ViewMode = '2d' | '3d'

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
  const [viewMode, setViewMode] = useState<ViewMode>('2d')
  const socket = useRef<SimulationSocket | null>(null)

  // Playback control state for Task 12: pause/step/replay
  const [isPlaying, setIsPlaying] = useState(true)
  const [bufferedEvents, setBufferedEvents] = useState<SimulationEvent[]>([])
  const allEventsRef = useRef<SimulationEvent[]>([])

  const start = useCallback((confirmed: ExtractedBrief) => {
    setBrief(confirmed)
    setRun({ ...INITIAL_RUN, status: 'running' })
    setPhase('run')
    setIsPlaying(true)
    setBufferedEvents([])
    allEventsRef.current = []

    socket.current?.close()
    socket.current = runSimulation(confirmed as unknown as Record<string, unknown>, {
      onEvent: (event) => {
        allEventsRef.current.push(event)

        // If paused, buffer the event instead of applying immediately
        if (!isPlaying) {
          setBufferedEvents((prev) => [...prev, event])
        } else {
          // Apply immediately when playing
          setRun((current) => reduceEvent(current, event))
        }
      },
      onError: (message) =>
        setRun((current) =>
          current.status === 'complete'
            ? current
            : { ...current, status: 'error', error: message },
        ),
    })
  }, [isPlaying])

  const answer = useCallback((decisionPointId: string, value: string) => {
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

  // Task 12 playback controls
  const handlePlayPause = useCallback((playing: boolean) => {
    setIsPlaying(playing)
    if (playing && bufferedEvents.length > 0) {
      // Apply all buffered events
      setRun((current) =>
        bufferedEvents.reduce((acc, evt) => reduceEvent(acc, evt), current),
      )
      setBufferedEvents([])
    }
  }, [bufferedEvents])

  const handleStep = useCallback(() => {
    if (bufferedEvents.length > 0) {
      const nextEvent = bufferedEvents[0]
      setRun((current) => reduceEvent(current, nextEvent))
      setBufferedEvents((prev) => prev.slice(1))
    }
  }, [bufferedEvents])

  const handleReplay = useCallback(() => {
    // Replay all events from the beginning
    setRun(
      allEventsRef.current.reduce((acc, evt) => reduceEvent(acc, evt), INITIAL_RUN),
    )
    setBufferedEvents([])
    setIsPlaying(true)
  }, [])

  const restart = useCallback(() => {
    socket.current?.stop()
    socket.current?.close()
    socket.current = null
    setRun(INITIAL_RUN)
    setIntake(null)
    setBrief(null)
    setPhase('intake')
    setIsPlaying(true)
    setBufferedEvents([])
    allEventsRef.current = []
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

  // Parse zones for 3D view
  const zones3d: Zone3D[] = (output?.zones ?? [])
    .map((raw) => parseZone(raw as Record<string, any>))
    .filter((z): z is Zone3D => z !== null)

  return (
    <div className="app">
      {run.status !== 'idle' && (
        <div className="view-mode-toggle" data-testid="view-mode-toggle">
          <button
            className={`view-btn ${viewMode === '2d' ? 'active' : ''}`}
            onClick={() => setViewMode('2d')}
            data-testid="view-2d-button"
            title="2D Process Flow"
          >
            📊 2D Flow
          </button>
          <button
            className={`view-btn ${viewMode === '3d' ? 'active' : ''}`}
            onClick={() => setViewMode('3d')}
            data-testid="view-3d-button"
            title="3D Build Model"
          >
            🏗️ 3D Model
          </button>
        </div>
      )}

      {viewMode === '2d' ? (
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
          aside={
            <RunPanel
              run={run}
              onAnswer={answer}
              onRestart={restart}
              isPlaying={isPlaying}
              onPlayPause={handlePlayPause}
              onStep={handleStep}
              onReplay={handleReplay}
            />
          }
          autoFit={run.status !== 'idle'}
        />
      ) : (
        <div className="view-3d-wrapper">
          <View3D zones={zones3d} />
          <div className="view-3d-sidebar">
            <RunPanel
              run={run}
              onAnswer={answer}
              onRestart={restart}
              isPlaying={isPlaying}
              onPlayPause={handlePlayPause}
              onStep={handleStep}
              onReplay={handleReplay}
            />
            {zones3d.length > 0 && (
              <div className="zones-list" style={{ marginTop: '1.5rem' }}>
                <h4 style={{ marginTop: 0 }}>Zones ({zones3d.length})</h4>
                <ul style={{ listStyle: 'none', margin: 0, padding: 0, fontSize: '11px' }}>
                  {zones3d.map((z) => (
                    <li
                      key={z.zone_id}
                      style={{
                        padding: '4px 6px',
                        borderLeft: `3px solid ${STAGE_COLORS[z.stage] ?? '#888'}`,
                        marginBottom: '2px',
                      }}
                    >
                      <div style={{ fontWeight: 600 }}>{z.name}</div>
                      <div style={{ color: 'var(--muted)', fontSize: '10px' }}>
                        {z.kind} • {z.stage.replace(/_/g, ' ')}
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
