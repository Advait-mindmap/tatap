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
/** How many times a dropped stream is picked back up before the reader is told.
 *
 * Bounded so a server closing every socket cannot spin, and generous enough that a
 * proxy trimming one idle connection is invisible - which is what usually happens. */
const MAX_RECONNECTS = 3

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

  /**
   * The run id in the address bar.
   *
   * Runs are durable on the server and `attach` has always existed, but nothing put the id
   * anywhere a browser could keep it: it lived in React state, so a refresh - or a crash, or
   * closing the tab, or sending someone the link - lost the only handle to a run that was
   * sitting on the server waiting to be answered. A halted run with no way back to it is worse
   * than a lost one, because the work is still there and still paid for.
   *
   * `replaceState` rather than `pushState`: this is where you already are, not a new place, and
   * Back should leave the app rather than walk through a run's history.
   */
  /**
   * A stream that ends because WE ended it, versus one that just stopped.
   *
   * A WebSocket that closes CLEANLY fires `close` and NOT `error`, and `runSimulation` accepted
   * an `onClose` handler that neither caller passed - so a clean close was discarded and the run
   * stayed `running` for ever. That is the reported failure: "Simulating…" with frozen counts,
   * no JS errors, no failed requests, while the server carried on and a decision waited.
   *
   * Clean closes are routine here. This stream goes quiet for long stretches while a stage
   * reasons - measured p90 11.9s, max 137s between events - and an idle socket of that length is
   * exactly what a proxy trims.
   */
  const attachRef = useRef<((runId: string) => void) | null>(null)
  const reconnects = useRef(0)
  const runIdRef = useRef<string | null>(null)
  //: Which connection is the live one. Every socket is opened with the token it was born under
  //: and reports it back on close, so a close can be attributed rather than guessed at.
  const connection = useRef(0)

  /**
   * Abandon the current socket and take a fresh token.
   *
   * The token is what makes a deliberate close distinguishable from a drop, and it is safe where
   * a flag was not: bumping it invalidates whatever socket was live, whether or not that socket
   * ever fires a close - and on the first run there is no socket to fire one.
   */
  const nextConnection = useCallback(() => {
    socket.current?.close()
    socket.current = null
    connection.current += 1
    return connection.current
  }, [])

  const rememberRun = useCallback((runId: string) => {
    if (!runId) return
    const url = new URL(window.location.href)
    if (url.searchParams.get('run') === runId) return
    url.searchParams.set('run', runId)
    window.history.replaceState(null, '', url.toString())
  }, [])

  const forgetRun = useCallback(() => {
    const url = new URL(window.location.href)
    if (!url.searchParams.has('run')) return
    url.searchParams.delete('run')
    window.history.replaceState(null, '', url.toString())
  }, [])

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

    const token = nextConnection()
    socket.current = runSimulation(confirmed as unknown as Record<string, unknown>, {
      onEvent: (event) => {
        // Recorded for replay, queued for drawing. Both unconditionally: what the server sent
        // is history, and whether it has been drawn yet is a separate question.
        allEventsRef.current.push(event)
        queueRef.current.push(event)
        setQueued(queueRef.current.length)
        // Put the id in the URL the moment the server issues it, not when the queue drains -
        // a run that dies mid-draw is exactly the one worth being able to get back to.
        const id = (event.payload as { run_id?: string } | undefined)?.run_id
        if (id) {
          runIdRef.current = id
          rememberRun(id)
        }
        // A delivered event means the connection works; the reconnect budget resets so a long
        // run is not capped by drops it already recovered from.
        reconnects.current = 0
      },
      onError: (message) =>
        setRun((current) =>
          current.status === 'complete'
            ? current
            : { ...current, status: 'error', error: message },
        ),
      onClose: () => handleClose(token),
    })
  }, [setPlaying, rememberRun])

  /**
   * The stream ended. Decide whether that was the end of the RUN or the end of the CONNECTION.
   *
   * A terminal event already received means the run finished or halted properly and the server
   * closed behind it - the queue may still be draining, so `status` is not a reliable signal
   * here and the event record is. Anything else is a dropped connection, and the run is still
   * out there: reattach, which is the same path a reload takes.
   *
   * Bounded, because a server that closes every socket immediately would otherwise spin. After
   * the last attempt the reader is told, which is the point - the old behaviour said nothing at
   * all, and a spinner that never resolves gives them nothing to notice.
   */
  const handleClose = useCallback((token: number) => {
    // A close from a socket we have already replaced. Starting or re-attaching opens a new
    // connection and abandons the old one, and the old one's close then arrives - about a run
    // nobody is watching any more.
    //
    // This began life as a latching boolean and that was wrong in a way worth recording: the
    // first `start()` calls `closeSocket()` before any socket exists, so the flag was armed and
    // NOTHING ever fired a close to clear it. The next drop - a real one - saw the flag set and
    // was swallowed, which is the exact bug this handler was written to fix, reintroduced by its
    // own guard. A token cannot latch: it either matches the live connection or it does not.
    if (token !== connection.current) return

    const finished = allEventsRef.current.some(
      (event) =>
        event.type === 'simulation_completed' ||
        event.type === 'simulation_halted' ||
        event.type === 'simulation_error',
    )
    if (finished) return

    const runId = runIdRef.current
    if (runId && reconnects.current < MAX_RECONNECTS) {
      reconnects.current += 1
      window.setTimeout(() => attachRef.current?.(runId), 400 * reconnects.current)
      return
    }
    setRun((current) =>
      current.status === 'complete'
        ? current
        : {
            ...current,
            status: 'error',
            error: runId
              ? `Lost the connection to the run after ${MAX_RECONNECTS} attempts to reconnect. ` +
                'The run is still on the server — reload to pick it up again.'
              : 'Lost the connection before the run was identified, so it cannot be recovered.',
          },
    )
  }, [])

  const answer = useCallback((decisionPointId: string, value: string) => {
    setRun((current) => {
      const remaining = current.openDecisions.filter((d) => d.id !== decisionPointId)
      return {
        ...current,
        status: remaining.length ? 'halted' : 'running',
        openDecisions: remaining,
        // Recorded here, not only when the server confirms. An authoritative halt that arrives
        // (or is drained) later still lists this fork as open, because it was open when the
        // server sent it - and re-offering an answered fork gets the run killed. This is the
        // client remembering what it has already said.
        answered: current.answered.some((a) => a.id === decisionPointId)
          ? current.answered
          : [...current.answered, { id: decisionPointId, answer: value }],
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

  /**
   * Reconnect to a run named in the URL.
   *
   * Used on load, and by the reconnect button when a socket drops. The backend rebuilds the run
   * from storage and replays the open forks, so this recovers a halted run whether the tab was
   * refreshed, the container restarted, or the link came from someone else.
   */
  const attachTo = useCallback((runId: string) => {
    runIdRef.current = runId
    setRun({ ...INITIAL_RUN, status: 'running', runId })
    setPhase('run')
    setPlaying(true)
    replayingRef.current = false
    queueRef.current = []
    setQueued(0)
    allEventsRef.current = []

    const token = nextConnection()
    socket.current = runSimulation(
      {},
      {
        onEvent: (event) => {
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
        onClose: () => handleClose(token),
      },
      { attachRunId: runId },
    )
  }, [setPlaying])

  // `handleClose` is defined before `attachTo` and must be able to call it. A ref keeps the
  // handler's identity stable, which matters because it is handed to a socket that outlives
  // renders.
  useEffect(() => {
    attachRef.current = attachTo
  }, [attachTo])

  // On load: if the address bar names a run, go straight to it instead of the intake screen.
  // Runs outlive the tab that started them, so the tab should not be the only way back.
  const attachedOnLoad = useRef(false)
  useEffect(() => {
    if (attachedOnLoad.current) return
    attachedOnLoad.current = true
    const runId = new URLSearchParams(window.location.search).get('run')
    if (runId) attachTo(runId)
  }, [attachTo])

  const restart = useCallback(() => {
    forgetRun()
    socket.current?.stop()
    nextConnection()
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
  }, [setPlaying, forgetRun])

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
      streaming={run.status === 'running'}
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
          <span className="view-toggle-label">View</span>
          <div className="view-segmented" role="tablist" aria-label="View mode">
          <button
            className={`view-btn ${viewMode === '2d' ? 'active' : ''}`}
            onClick={() => setViewMode('2d')}
            role="tab"
            aria-selected={viewMode === '2d'}
            data-testid="view-2d-button"
            title="2D Process Flow"
          >
            <span className="view-btn-glyph" aria-hidden="true">📊</span>
            <span className="view-btn-text">2D Flow</span>
          </button>
          <button
            className={`view-btn ${viewMode === '3d' ? 'active' : ''}`}
            onClick={() => setViewMode('3d')}
            role="tab"
            aria-selected={viewMode === '3d'}
            data-testid="view-3d-button"
            title="3D Build Model"
          >
            <span className="view-btn-glyph" aria-hidden="true">🏗️</span>
            <span className="view-btn-text">3D Model</span>
          </button>
          <button
            className={`view-btn ${viewMode === 'split' ? 'active' : ''}`}
            onClick={() => setViewMode('split')}
            role="tab"
            aria-selected={viewMode === 'split'}
            data-testid="view-split-button"
            title="Both views, linked by hover"
          >
            <span className="view-btn-glyph" aria-hidden="true">🔗</span>
            <span className="view-btn-text">Linked</span>
          </button>
          </div>
        </div>
      )}

      {viewMode === 'split' ? (
        <div className="view-split" data-testid="view-split">
          <div className="view-split-2d">{flowView}</div>
          <div className="view-split-3d">
            {model3d}
            {timeline.rfsDay > 0 && (
              <TimeScrubber
                timeline={timeline}
                day={day}
                onChange={setScrubDay}
                provisional={run.status !== 'complete'}
              />
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
              <TimeScrubber
                timeline={timeline}
                day={day}
                onChange={setScrubDay}
                provisional={run.status !== 'complete'}
              />
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
