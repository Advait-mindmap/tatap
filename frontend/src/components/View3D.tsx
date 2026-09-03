import { useEffect, useMemo } from 'react'
import { Canvas, useThree } from '@react-three/fiber'
import { Edges, OrbitControls } from '@react-three/drei'
import type { Zone3D } from '../viz3d'
import { STAGE_COLORS, ZONE_GEOMETRY, layoutZones, sceneBounds } from '../viz3d'
import { zoneStateAt, type Timeline, type ZoneState } from '../timeline'
import '../styles.css'

interface ZoneMeshProps {
  zone: Zone3D
  /** What the 4D timeline says is happening here on the selected day. */
  state: ZoneState
  /** True when this zone is the one linked from the 2D view (or hovered here). */
  linked: boolean
  /** True when some OTHER zone is linked, so this one should recede. */
  muted: boolean
  onHover: (zoneId: string | null) => void
}

/**
 * A single zone rendered as a box with stage-based colour.
 *
 * Outlined rather than glowing. The material used to be strongly emissive, which lit every face
 * identically and made a row of same-stage zones — fifteen of them are mep_power on a typical
 * brief — merge into one flat shape. Real shading plus an edge line is what makes them read as
 * separate boxes.
 */
function ZoneMesh({ zone, state, linked, muted, onHover }: ZoneMeshProps) {
  const color = STAGE_COLORS[zone.stage] ?? '#94a3b8'
  const geo = zone.size ?? ZONE_GEOMETRY[zone.kind]
  const x = zone.x ?? 0
  const z = zone.z ?? 0

  // Work in progress is drawn as a low, translucent shell - the footprint exists but the thing
  // is not built yet - and completed work is solid and full height. That difference is the
  // whole point of a 4D view: the model at day N is what exists at day N, not a picture of the
  // finished building shown early.
  const building = state === 'complete'
  // Under construction has to be VISIBLE, not merely different. At 18% height and 40% opacity
  // thirty-three zones going up changed the rendered image by less than frame noise - the 4D
  // view was technically correct and showed the viewer nothing. A third height at three
  // quarters opacity reads clearly as "footprint out of the ground, not finished".
  const height = building ? geo.height : Math.max(geo.height * 0.34, 1.2)

  return (
    <mesh
      position={[x, height / 2, z]}
      castShadow
      receiveShadow
      // The raycast pick VISUALIZATION_SPEC.md section 2 asks for. stopPropagation keeps the
      // pick to the nearest zone; without it the ray reports every box behind it too and the
      // site plane under everything wins.
      onPointerOver={(e) => {
        e.stopPropagation()
        onHover(zone.zone_id)
      }}
      onPointerOut={(e) => {
        e.stopPropagation()
        onHover(null)
      }}
      onClick={(e) => {
        e.stopPropagation()
        onHover(zone.zone_id)
      }}
    >
      <boxGeometry args={[geo.width, height, geo.depth]} />
      <meshStandardMaterial
        color={color}
        emissive={color}
        // Linked zones brighten; everything else recedes. Dimming rather than hiding, so the
        // reader keeps the context of where in the site the highlighted zone sits.
        emissiveIntensity={linked ? 0.95 : building ? 0.12 : 0.5}
        roughness={0.55}
        metalness={0.05}
        // The site is the ground the plan stands on, not a building. At full strength its
        // stage colour is the largest, loudest surface on screen and the buildings read as
        // detail on top of it; held back, it reads as the parcel it represents.
        transparent={zone.kind === 'site' || !building || muted}
        opacity={
          muted ? 0.16 : zone.kind === 'site' ? 0.45 : building ? 1 : 0.75
        }
      />
      <Edges
        scale={linked ? 1.03 : 1.001}
        threshold={15}
        color={linked ? '#f8fafc' : '#0b1220'}
      />
    </mesh>
  )
}

/** Grid floor for visual reference, sized to the plan it sits under. */
function Grid({ extent }: { extent: number }) {
  const span = Math.ceil(extent / 10) * 10 * 2
  return <gridHelper args={[span, span / 10]} position={[0, -0.6, 0]} />
}

/**
 * Lights for the 3D scene: directional (sun) + ambient.
 */
function Lights() {
  return (
    <>
      <ambientLight intensity={0.55} />
      <directionalLight
        position={[60, 90, 40]}
        intensity={1.1}
        castShadow
        shadow-mapSize-width={2048}
        shadow-mapSize-height={2048}
      />
      <directionalLight position={[-50, 40, -40]} intensity={0.35} />
    </>
  )
}

/**
 * Frame the whole plan on mount.
 *
 * The camera used to sit at a fixed (60, 50, 60) looking at the origin, which framed nothing in
 * particular: the model is as big as the brief makes it, so a constant position cropped larger
 * sites and stranded the plan half off-screen. Standing back by the plan's own radius fits it
 * whatever size it is.
 */
function FitCamera({ centre, radius }: { centre: [number, number, number]; radius: number }) {
  const { camera } = useThree()
  useEffect(() => {
    const distance = radius * 1.9
    camera.position.set(
      centre[0] + distance * 0.75,
      centre[1] + distance * 0.72,
      centre[2] + distance * 0.75,
    )
    if ('far' in camera) {
      camera.far = Math.max(2000, distance * 6)
      camera.updateProjectionMatrix()
    }
    camera.lookAt(centre[0], centre[1], centre[2])
  }, [camera, centre, radius])
  return null
}

interface View3DProps {
  zones: Zone3D[]
  /** Zone ids that at least one activity builds. Zones outside this set light nothing in 2D. */
  zonesWithWork?: Set<string>
  /** The 4D timeline, and the day being viewed. Absent means "show the finished model". */
  timeline?: Timeline
  day?: number
  /**
   * Live stage progress, while a run is streaming.
   *
   * Takes precedence over the timeline: during a run the model should follow the simulation,
   * not a scrubber position. A zone is absent until its stage starts, under construction while
   * that stage runs, and built when it completes - the 3D counterpart of the 2D flow drawing
   * itself node by node.
   */
  liveStages?: { started: string[]; completed: string[] } | null
  /** The zone highlighted from the 2D view, or from a pick here. */
  linkedZone?: string | null
  onHoverZone?: (zoneId: string | null) => void
}

/**
 * 3D schematic model of the data centre.
 *
 * Zones are rendered as boxes, colour-coded by stage. The camera orbits around
 * the site and building shell.
 */
export function View3D({
  zones, timeline, day, linkedZone = null, onHoverZone, zonesWithWork, liveStages = null,
}: View3DProps) {
  const positioned = useMemo(() => layoutZones(zones), [zones])
  const bounds = useMemo(() => sceneBounds(positioned), [positioned])

  // What each zone's state is right now.
  //
  // While a run streams, that is decided by the simulation: the stage that brings a zone into
  // existence has either not started, is running, or has completed. Once the run settles the
  // timeline takes over and the scrubber decides. With neither, the model shows the finished
  // facility, which is what a 3D-only view should do.
  const liveStarted = new Set(liveStages?.started ?? [])
  const liveCompleted = new Set(liveStages?.completed ?? [])

  const stateOf = (zone: Zone3D): ZoneState => {
    if (liveStages) {
      if (liveCompleted.has(zone.stage)) return 'complete'
      if (liveStarted.has(zone.stage)) return 'in_progress'
      return 'not_started'
    }
    return timeline && day !== undefined
      ? zoneStateAt(timeline.spans[zone.zone_id], day)
      : 'complete'
  }

  // Camera framing must not depend on the day: a zone appearing should not shove the view
  // around, or scrubbing looks like the camera is broken rather than the model changing.
  const built = positioned.filter((zone) => stateOf(zone) !== 'not_started')

  if (zones.length === 0) {
    return (
      <div className="view-3d-empty" data-testid="view-3d-empty">
        <p>No zones yet. Start a simulation to build the 3D model.</p>
      </div>
    )
  }

  return (
    <div className="view-3d-container" data-testid="view-3d">
      <Canvas
        className="view-3d-canvas"
        data-testid="view-3d-canvas"
        shadows
        camera={{ fov: 50, near: 0.1, far: 4000 }}
      >
        <color attach="background" args={['#0b1220']} />
        <FitCamera centre={bounds.centre} radius={bounds.radius} />
        <Lights />
        <Grid extent={bounds.radius} />
        {built.map((zone) => (
          <ZoneMesh
            key={zone.zone_id}
            zone={zone}
            state={stateOf(zone)}
            linked={linkedZone === zone.zone_id}
            muted={linkedZone !== null && linkedZone !== zone.zone_id}
            onHover={(id) => onHoverZone?.(id)}
          />
        ))}
        <OrbitControls
          makeDefault
          target={bounds.centre}
          enableDamping
          dampingFactor={0.08}
          enablePan
          enableZoom
          maxPolarAngle={Math.PI / 2.05}
        />
      </Canvas>

      <div className="view-3d-info">
        <h3>3D Build Model</h3>
        {linkedZone && (
          <p className="view-3d-linked" data-testid="linked-zone-label">
            {zones.find((z) => z.zone_id === linkedZone)?.name ?? linkedZone}
            {zonesWithWork && !zonesWithWork.has(linkedZone) && (
              // Say so rather than leaving the reader wondering why the flow did not react.
              // Most zones have no zone-tagged activities yet; that is a library gap, and
              // silence would read as a broken link.
              <span className="view-3d-linked-note" data-testid="linked-zone-no-work">
                {' '}
                — no activities reference this zone yet
              </span>
            )}
          </p>
        )}
        <p className="view-3d-zone-count" data-testid="zone-count">
          {timeline && day !== undefined
            ? `${built.length} of ${zones.length} zones · day ${day}`
            : `${zones.length} zones`}
        </p>
        {timeline && day !== undefined && (
          <p className="view-3d-zone-count" data-testid="built-count">
            {built.filter((z) => stateOf(z) === 'complete').length} complete ·{' '}
            {built.filter((z) => stateOf(z) === 'in_progress').length} in progress
          </p>
        )}
      </div>
    </div>
  )
}
