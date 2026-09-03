import { useCallback, useEffect, useRef, useState } from 'react'

import { runSimulation, type SimulationSocket } from './api'
import { FlowView } from './FlowView'
import { TimeScrubber } from './components/TimeScrubber'
import { View3D } from './components/View3D'
import { INITIAL_RUN, reduceEvent, type RunState } from './runState'
import { ConfirmScreen } from './screens/ConfirmScreen'
import { IntakeScreen } from './screens/IntakeScreen'
import { RunPanel } from './screens/RunPanel'
import type { ExtractedBrief, IntakeResult, SimulationEvent } from './types'
import { buildTimeline } from './timeline'
import { parseZone, STAGE_COLORS, type Zone3D } from './viz3d'
import './styles.css'

type Phase = 'intake' | 'confirm' | 'run'
// 'split' exists because linked hover is unobservable otherwise: highlighting a zone in the 3D
// model drives the 2D flow, and you cannot watch that happen through a tab switch.
type ViewMode = '2d' | '3d' | 'split'

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
  //: Which day the 4D model is showing. Null means "the finished plan", which is where a
  //: completed run should land: the scrubber is for looking back, not a state to be left in.
  const [scrubDay, setScrubDay] = useState<number | null>(null)
  //: The one shared highlight, VISUALIZATION_SPEC.md section 3's `highlight(ref)`. Either view
  //: may set it; both read it. Held here rather than in either view so neither owns the other.
  const [linkedZone, setLinkedZone] = useState<string | null>(null)
  const socket = useRef<SimulationSocket | null>(null)

  // ------------------------------------------------------------------ Task 12: playback
  //
  // Every event goes through one queue, and a timer drains it. That single path is what makes
  // pause, step and replay the same mechanism seen from three angles: pause stops the drain,
  // step drains exactly one, replay refills the queue from the recording and starts again.
  //
  // The flags live in refs as well as state. The socket's onEvent handler is registered once,
  // when the run starts, so it closes over whatever `isPlaying` was at that moment — and the
  // run always starts playing. Reading the boolean from state there meant the handler believed
  // the run was playing forever: pause buffered nothing and step had nothing to step through.
  // A ref is read at call time, so the handler sees the truth.
  const [isPlaying, setIsPlaying] = useState(true)
  const [queued, setQueued] = useState(0)
  const isPlayingRef = useRef(true)
  const queueRef = useRef<SimulationEvent[]>([])
  const replayingRef = useRef(false)
  const allEventsRef = useRef<SimulationEvent[]>([])

  /** Apply the next `n` queued events to the graph. The only place events reach the view. */
  const applyNext = useCallback((n: number) => {
    const batch = queueRef.current.splice(0, n)
    if (!batch.length) return
    setRun((current) => batch.reduce((acc, event) => reduceEvent(acc, event), current))
    setQueued(queueRef.current.length)
    if (!queueRef.current.length) replayingRef.current = false
  }, [])

  // The drain. One event per tick while replaying, so the build is watchable; during a live run
  // it catches up in proportion to the backlog, so streaming is not artificially slowed.
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!isPlayingRef.current || !queueRef.current.length) return
      const rate = replayingRef.current ? 1 : Math.max(1, Math.ceil(queueRef.current.length / 12))
      applyNext(rate)
    }, 40)
    return () => window.clearInterval(timer)
  }, [applyNext])

  const setPlaying = useCallback((playing: boolean) => {
    isPlayingRef.current = playing
    setIsPlaying(playing)
  }, [])

  const start = useCallback((confirmed: ExtractedBrief) => {
    setBrief(confirmed)
    setRun({ ...INITIAL_RUN, status: 'running' })
    setPhase('run')
    setPlaying(true)
    replayingRef.current = false
    queueRef.current = []
    setQueued(0)
    allEventsRef.current = []

    socket.current?.close()
    socket.current = runSimulation(confirmed as unknown as Record<string, unknown>, {
      onEvent: (event) => {
        // Recorded for replay, queued for drawing. Both unconditionally: what the server sent
        // is history, and whether it has been drawn yet is a separate question.
        allEventsRef.current.push(event)
        queueRef.current.push(event)
        setQueued(queueRef.current.length)
      },
      onError: (message) =>
        setRun((current) =>
          current.status === 'complete'
            ? current
            : { ...current, status: 'error', error: message },
        ),
    })
  }, [setPlaying])

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

  const handlePlayPause = useCallback((playing: boolean) => setPlaying(playing), [setPlaying])

  /** One event, once. Enabled only while paused, so it never races the drain. */
  const handleStep = useCallback(() => applyNext(1), [applyNext])

  /**
   * Rewind and rebuild.
   *
   * This used to reduce every recorded event in a single pass, which recomputed exactly the
   * state already on screen — a replay that was, visibly, nothing at all. Clearing the graph
   * and pushing the recording back through the same queue means the plan is drawn again the way
   * it was drawn the first time, which is the whole point of the control.
   */
  const handleReplay = useCallback(() => {
    setRun({ ...INITIAL_RUN, status: 'running' })
    queueRef.current = [...allEventsRef.current]
    replayingRef.current = true
    setQueued(queueRef.current.length)
    setPlaying(true)
  }, [setPlaying])

  const restart = useCallback(() => {
    socket.current?.stop()
    socket.current?.close()
    socket.current = null
    setRun(INITIAL_RUN)
    setIntake(null)
    setBrief(null)
    setPhase('intake')
    setPlaying(true)
    replayingRef.current = false
    queueRef.current = []
    setQueued(0)
    allEventsRef.current = []
  }, [setPlaying])

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

  // Parse zones for 3D view.
  //
  // The streamed site plan first: it arrives with simulation_started, whereas the authoritative
  // output only lands at a settle point. Preferring `output` here would leave the 3D model empty
  // for most of a run and then complete in one jump.
  const zones3d: Zone3D[] = ((output?.zones?.length ? output.zones : run.zones) ?? [])
    .map((raw) => parseZone(raw as Record<string, any>))
    .filter((z): z is Zone3D => z !== null)

  // The 4D timeline, straight from the engine's forward pass. Defaults to RFS so a finished run
  // opens on the finished building; dragging back is what shows the build.
  const timeline = buildTimeline(output)
  const day = scrubDay ?? timeline.rfsDay

  // While the run is still going, the 3D model follows the SIMULATION rather than the scrubber.
  // Once it completes the timeline takes over and the scrubber is in charge. A run the reader
  // has begun scrubbing is treated as finished with live mode, so dragging is never fought.
  const live =
    run.status !== 'idle' && run.status !== 'complete' && scrubDay === null
      ? { started: run.stagesStarted, completed: run.stagesCompleted }
      : null

  const runPanel = (
    <RunPanel
      run={run}
      onAnswer={answer}
      onRestart={restart}
      isPlaying={isPlaying}
      onPlayPause={handlePlayPause}
      onStep={handleStep}
      onReplay={handleReplay}
      queued={queued}
    />
  )

  const flowView = (
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
      aside={viewMode === 'split' ? undefined : runPanel}
      compact={viewMode === 'split'}
      autoFit={run.status !== 'idle'}
      linkedZone={linkedZone}
      onHoverZone={setLinkedZone}
    />
  )

  // Which zones any activity actually builds. Only a few activities carry a zone_id, so this
  // is a small set - and the 3D view needs it to explain a pick that lights nothing.
  const zonesWithWork = new Set(
    (output?.activities ?? [])
      .map((a) => String((a as Record<string, unknown>).zone_id ?? ''))
      .filter(Boolean),
  )

  const model3d = (
    <View3D
      zones={zones3d}
      zonesWithWork={zonesWithWork}
      liveStages={live}
      timeline={timeline}
      day={day}
      linkedZone={linkedZone}
      onHoverZone={setLinkedZone}
    />
  )

  return (
    // data-linked-zone is the shared highlight, exposed so the linked behaviour can be asserted
    // without reaching into React state.
    <div className="app" data-linked-zone={linkedZone ?? ''}>
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
          <button
            className={`view-btn ${viewMode === 'split' ? 'active' : ''}`}
            onClick={() => setViewMode('split')}
            data-testid="view-split-button"
            title="Both views, linked by hover"
          >
            🔗 Linked
          </button>
        </div>
      )}

      {viewMode === 'split' ? (
        <div className="view-split" data-testid="view-split">
          <div className="view-split-2d">{flowView}</div>
          <div className="view-split-3d">
            {model3d}
            {timeline.rfsDay > 0 && (
              <TimeScrubber timeline={timeline} day={day} onChange={setScrubDay} />
            )}
          </div>
        </div>
      ) : viewMode === '2d' ? (
        flowView
      ) : (
        <div className="view-3d-wrapper">
          <div className="view-3d-stack">
            {model3d}
            {timeline.rfsDay > 0 && (
              <TimeScrubber timeline={timeline} day={day} onChange={setScrubDay} />
            )}
          </div>
          <div className="view-3d-sidebar">
            {runPanel}
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
