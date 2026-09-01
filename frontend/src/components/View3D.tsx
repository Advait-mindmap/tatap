import { useEffect, useMemo } from 'react'
import { Canvas, useThree } from '@react-three/fiber'
import { Edges, OrbitControls } from '@react-three/drei'
import type { Zone3D } from '../viz3d'
import { STAGE_COLORS, ZONE_GEOMETRY, layoutZones, sceneBounds } from '../viz3d'
import '../styles.css'

interface ZoneMeshProps {
  zone: Zone3D
}

/**
 * A single zone rendered as a box with stage-based colour.
 *
 * Outlined rather than glowing. The material used to be strongly emissive, which lit every face
 * identically and made a row of same-stage zones — fifteen of them are mep_power on a typical
 * brief — merge into one flat shape. Real shading plus an edge line is what makes them read as
 * separate boxes.
 */
function ZoneMesh({ zone }: ZoneMeshProps) {
  const color = STAGE_COLORS[zone.stage] ?? '#94a3b8'
  const geo = zone.size ?? ZONE_GEOMETRY[zone.kind]
  const x = zone.x ?? 0
  const z = zone.z ?? 0

  return (
    <mesh
      position={[x, geo.height / 2, z]}
      castShadow
      receiveShadow
      onClick={(e) => {
        e.stopPropagation()
        console.log(`Clicked zone: ${zone.name}`)
      }}
    >
      <boxGeometry args={[geo.width, geo.height, geo.depth]} />
      <meshStandardMaterial
        color={color}
        emissive={color}
        emissiveIntensity={0.12}
        roughness={0.55}
        metalness={0.05}
        // The site is the ground the plan stands on, not a building. At full strength its
        // stage colour is the largest, loudest surface on screen and the buildings read as
        // detail on top of it; held back, it reads as the parcel it represents.
        transparent={zone.kind === 'site'}
        opacity={zone.kind === 'site' ? 0.45 : 1}
      />
      <Edges scale={1.001} threshold={15} color="#0b1220" />
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
}

/**
 * 3D schematic model of the data centre.
 *
 * Zones are rendered as boxes, colour-coded by stage. The camera orbits around
 * the site and building shell.
 */
export function View3D({ zones }: View3DProps) {
  const positioned = useMemo(() => layoutZones(zones), [zones])
  const bounds = useMemo(() => sceneBounds(positioned), [positioned])

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
        {positioned.map((zone) => (
          <ZoneMesh key={zone.zone_id} zone={zone} />
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
        <p className="view-3d-zone-count">{zones.length} zones</p>
      </div>
    </div>
  )
}
