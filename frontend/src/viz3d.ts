/**
 * 3D visualization constants and helpers for the data centre build model.
 *
 * Stages correspond to real construction phasing; colours distinguish progress through the build.
 * Zone kinds map to physical spaces (site, shell, data halls, electrical rooms, cooling plant).
 */

/** Stage order in the build timeline. */
export const STAGE_ORDER = [
  'enabling',
  'substructure',
  'superstructure',
  'envelope',
  'mep_power',
  'mep_cooling',
  'fire_bms',
  'fit_out',
  'commissioning',
]

/** Colour mapping: stage -> hex colour. Distinct hues for each major phase. */
export const STAGE_COLORS: Record<string, string> = {
  enabling: '#ef4444',        // red — early works
  substructure: '#f97316',    // orange — foundation
  superstructure: '#eab308',  // yellow — frame
  envelope: '#84cc16',        // lime — skin
  mep_power: '#06b6d4',       // cyan — electrical
  mep_cooling: '#0ea5e9',     // sky blue — cooling
  fire_bms: '#6366f1',        // indigo — systems
  fit_out: '#a855f7',         // purple — finish
  commissioning: '#22c55e',   // green — live
}

/**
 * Zone kinds and their display properties.
 *
 * Maps directly to backend/app/engine/zones.py ZONE_FIRST_STAGE.
 */
export const ZONE_KINDS = {
  site: { label: 'Site & external works', icon: '🏗️', order: 1 },
  shell: { label: 'Building shell & core', icon: '🏢', order: 2 },
  data_hall: { label: 'Data hall', icon: '🖥️', order: 3 },
  electrical_room: { label: 'Electrical room', icon: '⚡', order: 4 },
  ups_room: { label: 'UPS & battery room', icon: '🔋', order: 5 },
  generator_yard: { label: 'Generator yard', icon: '🔨', order: 6 },
  cooling_plant: { label: 'Cooling plant', icon: '❄️', order: 7 },
} as const

export type ZoneKind = keyof typeof ZONE_KINDS

/**
 * Geometry sizing by zone kind (in units; roughly 10m = 1 unit).
 *
 * These are parametric hints for the 3D model builder; actual dims come from zones[].
 */
export const ZONE_GEOMETRY: Record<ZoneKind, { width: number; depth: number; height: number }> = {
  site: { width: 50, depth: 50, height: 0.5 },
  shell: { width: 40, depth: 40, height: 8 },
  data_hall: { width: 12, depth: 12, height: 6 },
  electrical_room: { width: 8, depth: 8, height: 4 },
  ups_room: { width: 10, depth: 10, height: 4 },
  generator_yard: { width: 15, depth: 15, height: 4 },
  cooling_plant: { width: 20, depth: 20, height: 5 },
}


/**
 * Zone is a simplified runtime version of the backend Zone.
 *
 * Extends the backend zone with display-friendly fields.
 */
export interface Zone3D {
  zone_id: string
  name: string
  kind: ZoneKind
  stage: string
  /** Optional geometry hint from backend; used for position/size */
  geometry_ref?: string
  /** Computed by layoutZones: centre on the east-west axis. */
  x?: number
  /** Computed by layoutZones: centre on the north-south axis (three.js z, not height). */
  z?: number
  /** Computed footprint. Present so the site plane can be sized to whatever it has to hold. */
  size?: { width: number; depth: number; height: number }
}

/**
 * Parse a zone from the raw backend output.
 *
 * Validates that kind is a known zone type; returns null if unknown.
 *
 * The id is read from `id` — that is the key the engine emits (engine/ids.py `zone_id()`
 * writes it into `id`, not `zone_id`). Reading only `zone_id` gave every zone an empty
 * identity, which collapsed the React keys to a single value and left the model unable to tell
 * one box from another. `zone_id` is still accepted so a caller passing the display shape back
 * in round-trips cleanly.
 */
export function parseZone(raw: Record<string, any>): Zone3D | null {
  const kind = raw.kind as string
  if (!(kind in ZONE_KINDS)) {
    console.warn(`Unknown zone kind: ${kind}`)
    return null
  }

  return {
    zone_id: String(raw.id ?? raw.zone_id ?? ''),
    name: String(raw.name ?? ''),
    kind: kind as ZoneKind,
    stage: String(raw.stage ?? ''),
    geometry_ref: raw.geometry_ref ? String(raw.geometry_ref) : undefined,
  }
}

/** Clear ground between neighbouring zones, in model units (~10 m each). */
const GAP = 8
/** Zones per row before wrapping, so the plan stays roughly square rather than a long ribbon.
 *  Seven because the engine sizes most zone families per MW and lands on counts around that:
 *  wrapping at five split every family across two rows and made the site half as long again. */
const MAX_PER_ROW = 7

/**
 * Lay the zones out as a readable site plan.
 *
 * The rule that matters: **a cell is as big as the thing in it.** The previous layout advanced
 * by a fixed step (20 across, 8 down) regardless of footprint, so a 20-unit cooling plant and a
 * 15-unit generator yard on an 8-unit row pitch grew into each other — thirty-odd zones fused
 * into a handful of long slabs, and most of the site ran off the bottom of the camera. Spacing
 * derived from the geometry cannot do that.
 *
 * Zones are grouped by kind (all the data halls together, all the electrical rooms together),
 * which is both how a site is really organised and what makes a stage colour legible: a row of
 * one colour reads as a family, not as a mistake. Rows are laid north to south in the order the
 * build touches them, and the whole plan is centred on the origin so the camera has a fixed,
 * meaningful target.
 */
export function layoutZones(zones: Zone3D[]): Zone3D[] {
  const shell = zones.filter((z) => z.kind === 'shell')
  const others = zones.filter((z) => z.kind !== 'site' && z.kind !== 'shell')

  // Group by kind, ordered by how the build proceeds.
  const byKind = new Map<ZoneKind, Zone3D[]>()
  for (const zone of others) {
    const list = byKind.get(zone.kind) ?? []
    list.push(zone)
    byKind.set(zone.kind, list)
  }
  const groups = [...byKind.entries()].sort(
    (a, b) => ZONE_KINDS[a[0]].order - ZONE_KINDS[b[0]].order,
  )

  // The shell is the building everything else serves, so it leads the plan on its own row.
  const rows: Zone3D[][] = []
  if (shell.length) rows.push(shell)
  for (const [, list] of groups) {
    for (let i = 0; i < list.length; i += MAX_PER_ROW) {
      rows.push(list.slice(i, i + MAX_PER_ROW))
    }
  }

  const placed: Zone3D[] = []
  let cursorZ = 0

  for (const row of rows) {
    // Cell size comes from the largest member of THIS row, so a row of small rooms stays tight
    // and a row of plant gets the room it needs.
    const cellWidth = Math.max(...row.map((z) => ZONE_GEOMETRY[z.kind].width)) + GAP
    const rowDepth = Math.max(...row.map((z) => ZONE_GEOMETRY[z.kind].depth))
    const rowWidth = cellWidth * row.length
    const startX = -rowWidth / 2 + cellWidth / 2
    const centreZ = cursorZ + rowDepth / 2

    row.forEach((zone, index) => {
      placed.push({
        ...zone,
        x: startX + index * cellWidth,
        z: centreZ,
        size: ZONE_GEOMETRY[zone.kind],
      })
    })

    cursorZ += rowDepth + GAP
  }

  // Centre the plan on the origin so orbiting turns around the model rather than beside it.
  const depth = cursorZ - GAP
  const shiftZ = depth / 2
  const centred = placed.map((zone) => ({ ...zone, z: (zone.z ?? 0) - shiftZ }))

  // The site is the ground the rest stands on, so it is sized to contain the plan rather than
  // being a fixed square the buildings spill out of.
  const site = zones.find((z) => z.kind === 'site')
  if (site) {
    const halfWidth = Math.max(
      ...centred.map((z) => Math.abs(z.x ?? 0) + (z.size?.width ?? 0) / 2),
      20,
    )
    centred.unshift({
      ...site,
      x: 0,
      z: 0,
      size: {
        width: (halfWidth + GAP) * 2,
        depth: depth + GAP * 2,
        height: ZONE_GEOMETRY.site.height,
      },
    })
  }

  return centred
}

export interface SceneBounds {
  centre: [number, number, number]
  /** Half-diagonal of the plan: the distance a camera has to stand back to see all of it. */
  radius: number
}

/**
 * Where the camera should look, and how far back it has to stand.
 *
 * Derived from the laid-out zones rather than hard-coded, because the plan grows with the
 * brief: a 30 MW build has three times the data halls of a 10 MW one, and a fixed camera that
 * framed the small one cropped the large one. Computing it means the view fits whatever the
 * simulation produced.
 */
export function sceneBounds(zones: Zone3D[]): SceneBounds {
  if (!zones.length) return { centre: [0, 0, 0], radius: 60 }

  let minX = Infinity
  let maxX = -Infinity
  let minZ = Infinity
  let maxZ = -Infinity
  let maxY = 0

  for (const zone of zones) {
    const geo = zone.size ?? ZONE_GEOMETRY[zone.kind]
    const x = zone.x ?? 0
    const z = zone.z ?? 0
    minX = Math.min(minX, x - geo.width / 2)
    maxX = Math.max(maxX, x + geo.width / 2)
    minZ = Math.min(minZ, z - geo.depth / 2)
    maxZ = Math.max(maxZ, z + geo.depth / 2)
    maxY = Math.max(maxY, geo.height)
  }

  const width = maxX - minX
  const depth = maxZ - minZ
  return {
    centre: [(minX + maxX) / 2, maxY / 2, (minZ + maxZ) / 2],
    radius: Math.max(Math.hypot(width, depth) / 2, 20),
  }
}
